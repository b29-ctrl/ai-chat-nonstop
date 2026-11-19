#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次运行引导（onboarding）：装完第一次打开，告诉使用者「还差什么、怎么配」。

设计原则：
1. 只读检测，不改任何东西；唯一会写的是使用者点「开始使用」后记录的 onboarded 标记，
   而且写入交给调用方（服务.py 的 save_config），本模块不自己落盘。
2. 每项检测给三态：ok（能用）/ warn（能用但建议改）/ fail（这样下去用不了）。
3. 任何检测都不抛异常，出错就记成 fail 并带上原因——引导页本身不能崩。

用法（在服务.py 里）：
    import 引导
    rep = 引导.build_report(cfg, CONFIG_PATH, DATA, ROLE_CATALOG_PATH, APP_VERSION)
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
import time

OK, WARN, FAIL = "ok", "warn", "fail"

# 引导步骤：第一次用的人按这四步走完就能开工
STEPS = [
    {
        "no": 1,
        "title": "本地服务已经在跑",
        "body": "你看到的这个界面就是本机跑起来的服务。关掉窗口 = 退出，数据只存在本机，不上传。",
        "verify": "页面能打开、右上角版本号正常，就说明这一步完成了。",
    },
    {
        "no": 2,
        "title": "至少配一个能跑起来的 AI",
        "body": "平台自己不产出内容，靠下面这些执行端干活。先保证有 1 个是绿的，别全配。",
        "verify": "下方「AI 执行端」一项至少要有一个 ok。",
    },
    {
        "no": 3,
        "title": "发第一条工单试通",
        "body": "随便写一条：把要做的事、验收标准、产物放哪写清。工单会进队列，派发器自动找人干。",
        "verify": "工单状态从「待派」变成「进行中」或「已完成」，能看到 AI 的回话。",
    },
    {
        "no": 4,
        "title": "产物归档",
        "body": "任务结束时执行 AI 交一条回执（类型/项目/摘要/产物路径），由档案员按锚点写进记忆空间。"
                "没交回执的先自动抽一份进「待归档区」，你确认后才进正库。",
        "verify": "记忆空间的对应文件多出 1 行，原有内容没被改动。",
    },
]


def _item(cid, title, status, detail, fix=""):
    return {"id": cid, "title": title, "status": status, "detail": detail, "fix": fix}


def _check_env():
    ver = sys.version_info
    exe = sys.executable or "(未知)"
    bits = "64 位" if sys.maxsize > 2 ** 32 else "32 位"
    detail = "%s %d.%d.%d %s\n%s" % (platform.system(), ver.major, ver.minor, ver.micro, bits, exe)
    if ver < (3, 10):
        return _item("env", "运行环境", FAIL, detail,
                     "Python 版本过低，请安装 3.10 以上（推荐 3.12）。")
    if "WindowsApps" in exe or "Temp" in exe:
        return _item("env", "运行环境", WARN, detail,
                     "当前解释器像临时的，换成本机固定安装的 Python 更稳。")
    return _item("env", "运行环境", OK, detail)


def _check_data(data_dir):
    p = str(data_dir)
    try:
        os.makedirs(p, exist_ok=True)
        probe = os.path.join(p, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return _item("data", "数据目录可写", OK, p)
    except Exception as e:  # noqa: BLE001
        return _item("data", "数据目录可写", FAIL, "%s\n%s" % (p, e),
                     "目录不能写，台账和附件都存不下。检查权限或换一个有写权限的位置。")


def _resolvable(cmd0):
    """判断一个可执行字符串能不能找到。绝对路径看存在，命令名看 PATH。"""
    if not cmd0:
        return False, "空命令"
    if os.path.isabs(cmd0):
        return (os.path.exists(cmd0), cmd0)
    found = shutil.which(cmd0)
    return (bool(found), found or ("PATH 里找不到 %s" % cmd0))


def _check_adapters(cfg):
    ad = cfg.get("adapters") or {}
    paths = cfg.get("paths") or {}
    rows, usable = [], []
    for aid, a in ad.items():
        if not isinstance(a, dict):
            continue
        label = a.get("label") or aid
        if not a.get("enabled", False):
            rows.append("%s：未启用" % label)
            continue
        t = a.get("type")
        if t == "human":
            usable.append(aid)
            rows.append("%s：人类角色，无需检测" % label)
            continue
        if t == "process":
            cmd = a.get("command") or []
            c0 = cmd[0] if cmd else ""
            ok, where = _resolvable(c0)
            if ok:
                usable.append(aid)
                rows.append("%s：命令可用（%s）" % (label, where))
            else:
                rows.append("%s：命令找不到（%s）" % (label, where))
            continue
        if t == "workbuddy":
            cli = paths.get("workbuddy_cli") or ""
            key = paths.get("workbuddy_key") or ""
            bad = []
            if cli and not os.path.exists(cli):
                bad.append("workbuddy_cli 路径不存在")
            if key and os.path.isabs(key) and not os.path.exists(key):
                bad.append("workbuddy_key 文件不存在")
            if not cli:
                bad.append("没配 workbuddy_cli")
            if not bad:
                usable.append(aid)
                rows.append("%s：配置完整" % label)
            else:
                rows.append("%s：%s" % (label, "；".join(bad)))
            continue
        if t == "coze":
            if a.get("bot_id"):
                usable.append(aid)
                rows.append("%s：bot_id 已配" % label)
            else:
                rows.append("%s：缺 bot_id" % label)
            continue
        rows.append("%s：类型 %s 未做检查" % (label, t))

    # 重点提示：常见路径项在别人机器上大概率不存在
    hints = []
    for k in ("codex_cli", "dsh_cli", "workbuddy_cli"):
        v = paths.get(k) or ""
        if v and os.path.isabs(v) and not os.path.exists(v):
            hints.append("%s 指向的文件不存在：%s" % (k, v))

    detail = "\n".join(rows) if rows else "配置里还没有任何执行端"
    if hints:
        detail += "\n" + "\n".join(hints)

    if not usable:
        return _item("adapters", "AI 执行端", FAIL, detail,
                     "一个能用的都没有，平台发出去的工单没人接。先改 paths 里的路径，或先把某个执行端 enabled 打开。")
    nonger = [x for x in usable if (ad.get(x) or {}).get("type") != "human"]
    if not nonger:
        return _item("adapters", "AI 执行端", WARN, detail,
                     "现在只有人类角色，AI 不会真的干活。至少再配一个 AI 执行端。")
    return _item("adapters", "AI 执行端", OK, detail)


def _check_roles(role_path):
    p = str(role_path)
    if not os.path.exists(p):
        return _item("roles", "岗位目录", FAIL, "文件不存在：%s" % p,
                     "缺岗位目录，派单时没法给工单定岗位。从安装包里把 岗位目录.json 放回来。")
    try:
        import json
        with open(p, "r", encoding="utf-8-sig") as f:
            json.load(f)
        return _item("roles", "岗位目录", OK, p)
    except Exception as e:  # noqa: BLE001
        return _item("roles", "岗位目录", FAIL, "%s\n%s" % (p, e), "文件解析失败，可能被改坏了，用原始文件覆盖回去。")


def _check_memory(cfg, root_dir):
    """记忆空间库根：没有就说明首次使用，会自动建，所以只给 warn 不给 fail。"""
    mr = (cfg.get("memory_root") or "").strip()
    if mr:
        p = mr
    else:
        p = os.path.join(os.path.expanduser("~"), "Documents", "记忆空间")
    exists = os.path.isdir(p)
    structure = all(os.path.isdir(os.path.join(p, d)) for d in ("02_项目", "03_AI记忆", "04_技能", "06_产出"))
    if exists and structure:
        return _item("memory", "记忆空间", OK, p)
    if exists:
        return _item("memory", "记忆空间", WARN, "%s\n目录在，但结构不全（缺 02/03/04/06 中的某些目录）" % p,
                     "第一次归档时会自动补齐缺失目录，已有文件不会被覆盖。想换位置就在配置里设 memory_root。")
    return _item("memory", "记忆空间", WARN, "还没建：%s" % p,
                 "首次归档时会自动创建。想放别处，先在配置里加 memory_root 指向你的目录。")


def build_report(cfg, config_path, data_dir, role_catalog_path, app_version=""):
    items = []
    for fn in (
        lambda: _check_env(),
        lambda: _check_data(data_dir),
        lambda: _check_adapters(cfg),
        lambda: _check_roles(role_catalog_path),
        lambda: _check_memory(cfg, config_path),
    ):
        try:
            items.append(fn())
        except Exception as e:  # noqa: BLE001
            items.append(_item("err", "检测出错", FAIL, str(e), "把这段报错发出去排查。"))

    fails = [x for x in items if x["status"] == FAIL]
    warns = [x for x in items if x["status"] == WARN]
    first_run = not bool(cfg.get("onboarded"))

    return {
        "ok": True,
        "first_run": first_run,
        "app": cfg.get("app", {}).get("title") or "AI聊不停",
        "version": (cfg.get("app", {}).get("version") or app_version),
        "summary": {
            "fail": len(fails),
            "warn": len(warns),
            "ok": len([x for x in items if x["status"] == OK]),
        },
        "checks": items,
        "steps": STEPS,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config_path": str(config_path),
        "data_dir": str(data_dir),
    }


def mark(cfg):
    """返回写入 onboarded 标记后的配置副本，落盘由调用方做。"""
    cfg = dict(cfg)
    cfg["onboarded"] = True
    cfg["onboarded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return cfg


if __name__ == "__main__":
    import json
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "配置.json"), "r", encoding="utf-8-sig") as f:
        c = json.load(f)
    print(json.dumps(build_report(c, os.path.join(here, "配置.json"),
                                  os.path.join(here, "数据"),
                                  os.path.join(here, "岗位目录.json"),
                                  "0.6.0"), ensure_ascii=False, indent=2))
