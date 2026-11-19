# -*- coding: utf-8 -*-
"""AI聊不停 · 每日备份（C2，2026-11-13 DSH 补）

备份内容（默认写 ~/备份/每日/YYYY-MM-DD/）：
  1) 台账.sqlite3        —— 用 sqlite3 backup API 做一致性快照（服务运行中也安全）
  2) memsearch/          —— ~/.memsearch/（配置 + 索引库，如不存在自动跳过）
  3) 共享记忆库/          —— $AI_CHAT_SHARED_DIR（可选，如不存在自动跳过）
  4) DS记忆库/            —— $AI_CHAT_DS_DIR（可选，如不存在自动跳过）
  5) manifest.json       —— 条目清单：文件数 / 字节 / 关键文件 sha256 / 自检结果

保留：默认最近 7 天（--keep 可改）；日志：<root>/备份.log（>5MB 自动轮转 .log.1）。
用法：python 备份.py [--quiet] [--keep 7] [--root ~/备份] [--dry-run]
退出码：0=成功；1=有失败项（明细写日志）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "数据" / "台账.sqlite3"
HOME = Path.home()
MEMSEARCH_DIR = HOME / ".memsearch"
SHARED_DIR = Path(os.environ.get("AI_CHAT_SHARED_DIR") or (HOME / "共享记忆库"))
DS_DIR = Path(os.environ.get("AI_CHAT_DS_DIR") or (HOME / "DS记忆库"))
# 运行态关键配置（2026-11-13 补：CB 审查指出既有缺口）
CONFIG_FILES = (
    ("配置.json", APP_DIR / "配置.json"),
    ("岗位目录.json", APP_DIR / "岗位目录.json"),
)


def _log_path(root: Path) -> Path:
    return root / "备份.log"


def log(root: Path, msg: str, dry: bool = False) -> None:
    line = f"[{_dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    if dry:
        return
    try:
        root.mkdir(parents=True, exist_ok=True)
        lp = _log_path(root)
        if lp.exists() and lp.stat().st_size > 5 * 1024 * 1024:
            try:
                lp.replace(lp.with_suffix(".log.1"))
            except OSError:
                pass
        with lp.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def sha256_file(p: Path, limit: int = 0) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        while True:
            b = fh.read(1024 * 1024)
            if not b:
                break
            h.update(b)
            if limit:
                limit -= len(b)
                if limit <= 0:
                    break
    return h.hexdigest()


def dir_stats(d: Path) -> tuple[int, int]:
    n = 0
    size = 0
    if not d.exists():
        return 0, 0
    for f in d.rglob("*"):
        if f.is_file():
            n += 1
            try:
                size += f.stat().st_size
            except OSError:
                pass
    return n, size


def backup_sqlite(src: Path, dst: Path) -> dict:
    info: dict = {"name": "台账.sqlite3", "src": str(src), "ok": False}
    if not src.exists():
        info["error"] = "源不存在"
        return info
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        s = sqlite3.connect(str(src))
        d = sqlite3.connect(str(tmp))
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()
        os_replace = getattr(tmp, "replace")
        os_replace(dst)
        # 校验：完整性 + 关键表行数
        c = sqlite3.connect(str(dst))
        try:
            integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
            tables = ["projects", "tickets", "group_messages", "chat_messages", "signoffs"]
            counts = {}
            for t in tables:
                try:
                    counts[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except Exception:  # noqa: BLE001
                    counts[t] = None
            info.update({
                "ok": integrity == "ok",
                "integrity": integrity,
                "counts": counts,
                "bytes": dst.stat().st_size,
                "sha256": sha256_file(dst),
            })
        finally:
            c.close()
    except Exception as exc:  # noqa: BLE001
        info["error"] = repr(exc)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return info


def backup_dir(src: Path, dst: Path, label: str) -> dict:
    info: dict = {"name": label, "src": str(src), "ok": False}
    if not src.exists():
        info["error"] = "源不存在"
        return info
    try:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True)
        n, size = dir_stats(dst)
        info.update({"ok": True, "files": n, "bytes": size})
        if label == "memsearch":
            for key in ("config.toml",):
                f = dst / key
                if f.exists():
                    info["sha256_" + key.replace(".", "_")] = sha256_file(f)
    except Exception as exc:  # noqa: BLE001
        info["error"] = repr(exc)
    return info


def backup_file(src: Path, dst: Path, label: str) -> dict:
    """单文件备份（配置类）：复制 + 记录 sha256。"""
    info: dict = {"name": label, "src": str(src), "ok": False}
    if not src.exists():
        info["error"] = "源不存在"
        return info
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        info.update({
            "ok": True,
            "dst": str(dst),
            "bytes": dst.stat().st_size,
            "sha256": sha256_file(dst),
        })
    except Exception as exc:  # noqa: BLE001
        info["error"] = repr(exc)
    return info


def cleanup_old(root: Path, keep: int, today: Path) -> list[str]:
    removed: list[str] = []
    daily = root / "每日"
    if not daily.is_dir():
        return removed
    cutoff = _dt.date.today() - _dt.timedelta(days=max(1, keep) - 1)
    for child in daily.iterdir():
        if not child.is_dir() or child == today:
            continue
        try:
            d = _dt.date.fromisoformat(child.name)
        except ValueError:
            continue
        if d < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--keep", type=int, default=7)
    ap.add_argument("--root", default=str(HOME / "备份"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)
    today = _dt.date.today()
    dest = root / "每日" / today.isoformat()
    dry = args.dry_run

    if not args.quiet:
        log(root, f"备份开始 keep={args.keep} root={root} dry={dry}", dry)
    if dry:
        for label, src in (("台账", DB_PATH), ("memsearch", MEMSEARCH_DIR),
                           ("共享记忆库", SHARED_DIR), ("DS记忆库", DS_DIR),
                           ("配置.json", APP_DIR / "配置.json"),
                           ("岗位目录.json", APP_DIR / "岗位目录.json")):
            log(root, f"[dry] {label} <- {src}（存在={src.exists()}）", dry)
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    t0 = time.time()

    results.append(backup_sqlite(DB_PATH, dest / "台账.sqlite3"))
    results.append(backup_dir(MEMSEARCH_DIR, dest / "memsearch", "memsearch"))
    results.append(backup_dir(SHARED_DIR, dest / "共享记忆库", "共享记忆库"))
    results.append(backup_dir(DS_DIR, dest / "DS记忆库", "DS记忆库"))
    for name, src in CONFIG_FILES:
        results.append(backup_file(src, dest / "配置" / name, name))

    removed = cleanup_old(root, args.keep, dest)
    failed = [r for r in results if not r.get("ok") and r.get("error") != "源不存在"]
    manifest = {
        "date": today.isoformat(),
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - t0, 2),
        "items": results,
        "removed_old": removed,
        "ok": not failed,
    }
    try:
        (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log(root, f"manifest 写入失败：{exc!r}", dry)

    log(root, f"备份完成 date={today} 耗时={manifest['elapsed_s']}s 成败={'OK' if not failed else 'FAIL ' + str(len(failed))} 清理旧={removed}", dry)
    for r in results:
        log(root, f"  - {r['name']}: ok={r.get('ok')} bytes={r.get('bytes')} files={r.get('files')} {('error=' + r['error']) if r.get('error') else ''}", dry)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
