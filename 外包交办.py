#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""外包交办（协作文档交接）—— 对外包工（纯桌面端、无接口，如豆包）

方式沿用团队既有做法：DSH 在协作文档目录写任务书 → 主理人把文件交给桌面端 AI
→ 对方按约定写回报文档 → DSH 扫描协作文档目录回收、复核、登记。

目录：
  ~/项目目录/协作/        任务书与回报文档（共享交接区，可用 AI_CHAT_DOC_DIR 覆盖）
  <桌面端>\\数据\\外包\\index.json  台账（编号/任务书/回报/状态）

用法：
  python 外包交办.py new --title "任务标题" --to "豆包" --brief "要做什么" ^
        --inputs "~/a.md;~/b.xlsx" --output "交付要求" --deadline "2026-11-13 18:00"
  python 外包交办.py list
  python 外包交办.py check          # 扫描协作目录，匹配回报文档，更新台账
  python 外包交办.py accept --id OUT-20261113-01 --note "复核通过"
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys

BASE = pathlib.Path(__file__).resolve().parent
DOC_DIR = pathlib.Path(os.environ.get("AI_CHAT_DOC_DIR") or (BASE.parent / "协作")).expanduser()
OUT_DIR = BASE / "数据" / "外包"
INDEX = OUT_DIR / "index.json"


def _load() -> dict:
    if INDEX.exists():
        return json.loads(INDEX.read_text(encoding="utf-8"))
    return {"tasks": []}


def _save(db: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "-", s).strip("-")[:40]


def _next_id(now: dt.date, db: dict) -> str:
    prefix = f"OUT-{now:%Y%m%d}-"
    used = {t["id"] for t in db["tasks"]}
    n = 1
    while f"{prefix}{n:02d}" in used:
        n += 1
    return f"{prefix}{n:02d}"


def cmd_new(a) -> int:
    now = dt.datetime.now()
    db = _load()
    oid = _next_id(now.date(), db)
    inputs = [x.strip() for x in (a.inputs or "").replace("；", ";").split(";") if x.strip()]
    task_path = DOC_DIR / f"{now:%Y%m%d}_DSH_给外包_{_safe(a.title)}_任务书.md"
    report_path = DOC_DIR / f"{now:%Y%m%d}_{_safe(a.to)}_给DSH_{_safe(a.title)}_回报.md"
    body = f"""# 外包任务书：{a.title}

- 任务号：{oid}
- 日期：{now:%Y-%m-%d %H:%M}
- 下发：DSH（主理人）
- 接单：{a.to}（外包工·纯桌面端，无接口，协作文档交接）
- 截止：{a.deadline or '-'}

## 一、目标

{a.brief or '（见产出要求）'}

## 二、背景与上下文

- 项目：工单调度台 / AI聊不停
- 说明：本任务不依赖平台接口，按文档交接；有疑问在回报文档里写清楚，不要自行改需求。

## 三、输入文件（绝对路径，按需读取；读不到就在回报里说明）

{chr(10).join('- ' + p for p in inputs) if inputs else '- （无，内容都在任务书中）'}

## 四、产出要求

{a.output or '按目标给出可直接使用的结果；如需文件，写明完整内容或保存位置。'}

## 五、回执与交接

1. 完成后把结果写到本目录回报文档：`{report_path}`；
2. 回报文档头部四行：任务号 / 结论 / 产物（路径或内容） / 依据与自检；
3. 正文写清：做了什么、产物在哪、没做完的部分与原因、风险；
4. 不要签收、不要改 `F:\\共享记忆库`、不要写 `G:\\DS记忆库`、不要读凭据/密钥；
5. 主理人把回报文档交回 DSH，DSH 复核后登记入账。

## 六、验收口径（DSH 复核）

- 产出可用、路径真实存在；
- 与目标一致、未擅自扩展；
- 回执四行头齐全。
"""
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(body, encoding="utf-8")
    db["tasks"].append({
        "id": oid, "title": a.title, "to": a.to,
        "created": now.isoformat(timespec="seconds"),
        "deadline": a.deadline, "status": "待回报",
        "task_file": str(task_path), "report_file": str(report_path),
    })
    _save(db)
    print(f"[new] {oid}")
    print(f"任务书：{task_path}")
    print(f"回报文档应写到：{report_path}")
    print("—— 把任务书文件交给外包 AI；对方完成后按约定写回报文档 ——")
    print(f"回收：python 外包交办.py check")
    return 0


def _scan(db: dict) -> list:
    if not DOC_DIR.exists():
        return []
    reports = sorted(DOC_DIR.glob("*_给DSH_*_回报.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    hits = []
    for t in db["tasks"]:
        rp = pathlib.Path(t["report_file"])
        if rp.exists():
            hits.append((t, rp))
    return hits


def cmd_check(a) -> int:
    db = _load()
    hits = _scan(db)
    changed = 0
    for t, rp in hits:
        if t["status"] != "已回报":
            t["status"] = "已回报"
            t["report_seen"] = dt.datetime.now().isoformat(timespec="seconds")
            changed += 1
    if changed:
        _save(db)
    if not db["tasks"]:
        print("（暂无外包任务）")
        return 0
    for t in db["tasks"]:
        flag = "✅ 已回报" if pathlib.Path(t["report_file"]).exists() else "⏳ 待回报"
        print(f"{t['id']}  {flag}  {t['title']}  ->  {t['task_file']}")
        if pathlib.Path(t["report_file"]).exists():
            print(f"          回报：{t['report_file']}")
    return 0


def cmd_list(a) -> int:
    return cmd_check(a)


def cmd_accept(a) -> int:
    db = _load()
    for t in db["tasks"]:
        if t["id"] == a.id:
            t["status"] = "已验收"
            t["accept_note"] = a.note or ""
            t["accepted_at"] = dt.datetime.now().isoformat(timespec="seconds")
            _save(db)
            print(f"[accept] {a.id} 已验收；下一步登记 02_交付档案/记忆格。")
            return 0
    print(f"[accept] 未找到 {a.id}", file=sys.stderr)
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description="外包交办（协作文档交接）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("new", help="生成外包任务书到协作文档目录")
    p1.add_argument("--title", required=True)
    p1.add_argument("--to", default="豆包")
    p1.add_argument("--brief", default="")
    p1.add_argument("--inputs", default="")
    p1.add_argument("--output", default="")
    p1.add_argument("--deadline", default="")
    p1.set_defaults(func=cmd_new)

    p2 = sub.add_parser("check", help="扫描协作文档，更新回报状态")
    p2.set_defaults(func=cmd_check)
    p3 = sub.add_parser("list", help="列出外包任务")
    p3.set_defaults(func=cmd_list)
    p4 = sub.add_parser("accept", help="标记已验收")
    p4.add_argument("--id", required=True)
    p4.add_argument("--note", default="")
    p4.set_defaults(func=cmd_accept)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
