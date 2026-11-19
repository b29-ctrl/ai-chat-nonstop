# -*- coding: utf-8 -*-
r"""AI聊不停 · 本机巡检（不联网，替代 agent-autoloop 的三条 cron 规则）

用法：
  python 巡检.py --daily          每日巡检（建议每天 08:00）
  python 巡检.py --pending        待验提醒（建议每 2 小时）
  python 巡检.py --outbox         回执跟进（建议每 15 分钟）
  python 巡检.py --once --toast   立即跑全部；--toast 弹 Windows 通知
  python 巡检.py --daily --no-toast

只读：先把 台账.sqlite3(+wal/shm) 复制到临时目录再查，不占用、不写真实库。
产物：巡检\每日_YYYY-MM-DD.md、巡检\巡检.log、巡检\state.json（去重状态）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
DB = APP / "数据" / "台账.sqlite3"
OUT = APP / "巡检"
LOG = OUT / "巡检.log"
STATE = OUT / "state.json"
BACKUP_ROOT = Path(r"G:\备份")
NOW = dt.datetime.now().astimezone()


def refresh_now() -> None:
    """长驻循环里必须每次刷新 NOW，否则 age_min / 日报文件名全部停在进程启动那一刻。"""
    global NOW
    NOW = dt.datetime.now().astimezone()


def log(msg: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    line = f"[{NOW:%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        if LOG.exists() and LOG.stat().st_size > 5 * 1024 * 1024:
            LOG.replace(LOG.with_suffix(".log.1"))
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(st: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def read_db() -> sqlite3.Connection:
    """复制到临时目录读取（含 WAL/SHM），避免影响真实库。"""
    tmp = Path(tempfile.mkdtemp(prefix="patrol_"))
    dst = tmp / "db.sqlite3"
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(DB) + suffix)
        if src.exists():
            shutil.copy2(src, Path(str(dst) + suffix))
    conn = sqlite3.connect(str(dst))
    conn.row_factory = sqlite3.Row
    return conn


def parse_ts(v: str | None) -> dt.datetime | None:
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(v)
    except ValueError:
        return None


def age_min(v: str | None) -> float | None:
    t = parse_ts(v)
    if not t:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=NOW.tzinfo)
    return round((NOW - t).total_seconds() / 60, 1)


def toast(title: str, text: str) -> None:
    ps = r"""
Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.ShowBalloonTip(8000, '{0}', '{1}', [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Seconds 9
$n.Dispose()
""".format(title.replace("'", "''"), text.replace("'", "''"))
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def collect() -> dict:
    conn = read_db()
    try:
        def rows(sql: str, args=()) -> list[sqlite3.Row]:
            return conn.execute(sql, args).fetchall()

        counts = {r["status"]: r["n"] for r in rows("SELECT status, COUNT(*) n FROM tickets GROUP BY status")}
        pending = [dict(r) for r in rows("SELECT id,title,executor,gate,updated_at FROM tickets WHERE status='待验' ORDER BY updated_at")]
        failed = [dict(r) for r in rows("SELECT id,title,executor,updated_at FROM tickets WHERE status='失败' ORDER BY updated_at DESC LIMIT 20")]
        stuck = [dict(r) for r in rows(
            "SELECT id,title,executor,status,updated_at FROM tickets WHERE status IN ('派发中','已回件') ORDER BY updated_at")]
        projects = [dict(r) for r in rows("SELECT id,name,status,archived FROM projects ORDER BY created_at DESC")]
        last_group = rows("SELECT max(created_at) m FROM group_messages")[0]["m"] if rows("SELECT 1 FROM sqlite_master WHERE name='group_messages'") else None
    finally:
        conn.close()
    for item in pending + failed + stuck:
        item["age_min"] = age_min(item.get("updated_at"))
    return {"counts": counts, "pending": pending, "failed": failed, "stuck": stuck,
            "projects": projects, "last_group": last_group}


def backup_status() -> str:
    daily = BACKUP_ROOT / "每日"
    if not daily.is_dir():
        return "未发现备份目录"
    dirs = sorted([d for d in daily.iterdir() if d.is_dir()], reverse=True)
    if not dirs:
        return "备份目录为空"
    latest = dirs[0]
    mf = latest / "manifest.json"
    if not mf.exists():
        return f"最新备份 {latest.name} 无 manifest"
    try:
        man = json.loads(mf.read_text(encoding="utf-8"))
        return f"最新备份 {latest.name} ok={man.get('ok')} 耗时={man.get('elapsed_s')}s"
    except Exception as exc:  # noqa: BLE001
        return f"最新备份 {latest.name} manifest 损坏：{exc!r}"


def check_pending(data: dict, st: dict, do_toast: bool) -> list[str]:
    lines: list[str] = []
    pending = data["pending"]
    if not pending:
        return lines
    for t in pending:
        lines.append(f"- 待验 {t['id']} 「{t['title']}」 执行端={t['executor']} 更新于 {t['updated_at']}（已 {t['age_min']} 分钟）")
    if do_toast:
        ids = [t["id"] for t in pending]
        last = st.get("pending_toast") or {}
        if ids != last.get("ids"):
            toast("AI聊不停 · 待验提醒", f"{len(pending)} 单待签收：{pending[0]['id']} 等")
            st["pending_toast"] = {"ids": ids, "at": NOW.isoformat(timespec="seconds")}
    return lines


def check_outbox(data: dict, st: dict, do_toast: bool) -> list[str]:
    lines: list[str] = []
    stale = [t for t in data["stuck"]
             if (t["status"] == "派发中" and (t["age_min"] or 0) > 10)
             or (t["status"] == "已回件" and (t["age_min"] or 0) > 30)]
    for t in stale:
        hint = "检查派发器/模型" if t["status"] == "派发中" else "已回件未转待验？"
        lines.append(f"- {t['status']} {t['id']} 「{t['title']}」 执行端={t['executor']} 已 {t['age_min']} 分钟（{hint}）")
    for t in data["failed"][:10]:
        lines.append(f"- 失败 {t['id']} 「{t['title']}」 执行端={t['executor']} 更新于 {t['updated_at']}")
    if do_toast and (stale or data["failed"]):
        toast("AI聊不停 · 回执跟进", f"派发/回件停滞 {len(stale)} 单，失败 {len(data['failed'])} 单")
    return lines


def check_daily(data: dict) -> list[str]:
    c = data["counts"]
    lines = [
        f"# 每日巡检 {NOW:%Y-%m-%d}",
        "",
        "## 工单状态",
        f"- 待处理 {c.get('待处理', 0)}｜派发中 {c.get('派发中', 0)}｜已回件 {c.get('已回件', 0)}｜待验 {c.get('待验', 0)}｜已验 {c.get('已验', 0)}｜失败 {c.get('失败', 0)}",
        f"- 项目数 {len(data['projects'])}（进行中 {sum(1 for p in data['projects'] if p['status'] == 'running')}，已归档 {sum(1 for p in data['projects'] if p.get('archived'))}）",
        f"- 最近一条群消息：{data['last_group'] or '无'}",
        "",
        "## 备份",
        f"- {backup_status()}",
        "",
    ]
    if data["pending"]:
        lines.append("## 待签收")
        lines += [f"- {t['id']} 「{t['title']}」 已等 {t['age_min']} 分钟" for t in data["pending"]]
        lines.append("")
    if data["failed"]:
        lines.append("## 失败（最近）")
        lines += [f"- {t['id']} 「{t['title']}」 {t['updated_at']}" for t in data["failed"][:10]]
        lines.append("")
    return lines


def run_once(do_toast: bool = True, which=("daily", "pending", "outbox")) -> int:
    """跑一轮巡检。CLI 与长驻循环共用这一份实现。"""
    refresh_now()
    which = set(which)
    data = collect()
    st = load_state()
    OUT.mkdir(parents=True, exist_ok=True)

    if "daily" in which:
        lines = check_daily(data)
        (OUT / f"每日_{NOW:%Y-%m-%d}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"每日巡检已生成（待验 {len(data['pending'])}，失败 {len(data['failed'])}）")
        st["last_daily"] = NOW.isoformat(timespec="seconds")
        if do_toast:
            toast("AI聊不停 · 每日巡检", f"待验 {len(data['pending'])}｜失败 {len(data['failed'])}｜{backup_status()}")
    if "pending" in which:
        lines = check_pending(data, st, do_toast)
        if lines:
            log("待验提醒：\n" + "\n".join(lines))
        else:
            log("待验提醒：无待验单")
    if "outbox" in which:
        lines = check_outbox(data, st, do_toast)
        if lines:
            log("回执跟进：\n" + "\n".join(lines))
        else:
            log("回执跟进：无停滞/失败")
    save_state(st)
    return 0


def _acquire_single_instance(port: int = 18766):
    """用本地端口占位做单实例锁：多个服务实例同时开时只让一个跑巡检。"""
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sk.bind(("127.0.0.1", port))
        sk.listen(1)
        return sk
    except OSError:
        try:
            sk.close()
        except OSError:
            pass
        return None


def loop(outbox_every_min: int = 15, pending_every_min: int = 120,
         daily_at: tuple[int, int] = (8, 0), do_toast: bool = True) -> None:
    """随主服务启动的巡检循环。服务进程在就跑，服务退出即停，不注册任何计划任务。"""
    guard = _acquire_single_instance()
    if guard is None:
        log("巡检循环：检测到已有实例在跑，本进程跳过（不影响主服务）")
        return
    log(
        f"巡检循环已启动：回执每 {outbox_every_min} 分钟 / 待验每 {pending_every_min} 分钟 "
        f"/ 每日 {daily_at[0]:02d}:{daily_at[1]:02d}（随服务启动，服务退出即停）"
    )
    last_outbox = last_pending = None
    last_daily = ""
    while True:
        try:
            now = dt.datetime.now()
            due: list[str] = []
            if last_outbox is None or (now - last_outbox).total_seconds() >= outbox_every_min * 60:
                due.append("outbox")
                last_outbox = now
            if last_pending is None or (now - last_pending).total_seconds() >= pending_every_min * 60:
                due.append("pending")
                last_pending = now
            today = now.strftime("%Y-%m-%d")
            hm = (now.hour, now.minute)
            if hm >= daily_at and last_daily != today:
                due.append("daily")
                last_daily = today
            if due:
                run_once(do_toast=do_toast, which=due)
        except Exception as exc:  # noqa: BLE001 —— 巡检出错绝不能带崩主服务
            try:
                log(f"巡检循环异常（已忽略，下轮继续）：{exc!r}")
            except Exception:  # noqa: BLE001
                pass
        time.sleep(30)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--outbox", action="store_true")
    ap.add_argument("--once", action="store_true", help="三项都跑")
    ap.add_argument("--toast", action="store_true")
    ap.add_argument("--no-toast", action="store_true")
    ap.add_argument("--loop", action="store_true", help="长驻模式：随服务启动，按周期巡检")
    ap.add_argument("--loop-interval", type=int, default=15, help="回执跟进周期（分钟），配合 --loop")
    args = ap.parse_args()
    do_toast = args.toast and not args.no_toast

    if args.loop:
        loop(outbox_every_min=args.loop_interval, do_toast=do_toast)
        return 0

    if not any([args.daily, args.pending, args.outbox, args.once]):
        ap.print_help()
        return 2

    which = []
    if args.daily or args.once:
        which.append("daily")
    if args.pending or args.once:
        which.append("pending")
    if args.outbox or args.once:
        which.append("outbox")
    return run_once(do_toast=do_toast, which=which)


if __name__ == "__main__":
    sys.exit(main())
