#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI聊不停 · MemSearch 长期记忆接入（M2-MEM-FIX：串行按需索引，无常驻 watch）。

零第三方依赖：仅 Python 标准库（subprocess / json / shutil / threading /
pathlib / hashlib / datetime / os / sys / re / time）。

设计口径（见 M2-MEM-FIX 任务书 §1）：
  - Milvus Lite 对 milvus.db 是「单进程独占锁」。常驻 memsearch watch 会持有
    数据库，使同库 search 全部启动失败 → 召回静默全空。故本模块不启动任何
    长期存活的 memsearch 子进程。
  - 所有 memsearch 子进程调用（index / search）统一经模块级 _CLI_LOCK 串行，
    避免同一服务实例内自己抢锁。
  - 命中锁冲突字样时释放锁 → sleep 1.5s → 最多重试 2 次（共 3 次尝试）；
    重试期间不持有 _CLI_LOCK；仍失败才降级为空并记「锁冲突重试后仍失败」。
  - 启动时在后台线程跑一次 index；写回成功后用 Timer 合并 debounce 索引。
  - 召回失败一律降级为空字符串，绝不抛异常、绝不阻塞聊天。
  - 写回内容逐行脱敏；日志 > 2MB 轮转为 记忆.log.1。
  - 默认由 服务.py 控制开关（memory.enabled=false 时所有函数直接 return）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_LOG_PATH = Path(os.environ.get("MEM_LOG_PATH") or (ROOT / "数据" / "记忆.log"))
_LOG_MAX_BYTES = 2 * 1024 * 1024
_INDEX_TIMEOUT = 120.0
_RETRY_SLEEP_S = 1.5
_RETRY_MAX = 2

# 进程内串行：同一服务实例内 index / search 不重叠（Milvus Lite 独占锁）
_CLI_LOCK = threading.Lock()
# 写回 debounce：每项目一个 Timer，已有未触发的 Timer 就合并
_INDEX_TIMERS: dict[str, threading.Timer] = {}
_TIMER_LOCK = threading.Lock()
# 启动后台索引线程（一次性，非常驻）
_INDEX_THREADS: list[threading.Thread] = []
_THREAD_LOCK = threading.Lock()

_CLI_WARNED = False
_LOG_LOCK = threading.Lock()

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9_\-\.=]{8,}|[A-Za-z0-9_\-]{40,})")
_LOCK_RE = re.compile(r"Failed to start MilvusLite|acquire_lock|already", re.I)


def _log(msg: str) -> None:
    try:
        with _LOG_LOCK:
            # 日志轮转：> 2MB 时把 记忆.log 改名为 记忆.log.1（覆盖旧 .1）
            try:
                if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _LOG_MAX_BYTES:
                    bak = Path(str(_LOG_PATH) + ".1")
                    try:
                        if bak.exists():
                            bak.unlink()
                    except Exception:
                        pass
                    try:
                        os.replace(str(_LOG_PATH), str(bak))
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            with open(_LOG_PATH, "a", encoding="utf-8", newline="\n") as f:
                f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass


def _warn_once(msg: str) -> None:
    global _CLI_WARNED
    if not _CLI_WARNED:
        _CLI_WARNED = True
        _log(msg)


def _mem(cfg: dict) -> dict:
    return cfg.get("memory") or {}


def _cli_path(cfg: dict):
    cli = (_mem(cfg).get("cli") or "memsearch") if cfg else "memsearch"
    try:
        return shutil.which(str(cli))
    except Exception:
        return None


def _source_dir(cfg: dict, project_dir: str) -> str:
    sd = (_mem(cfg).get("source_dir") or "")
    if sd:
        return os.path.expandvars(os.path.expanduser(str(sd)))
    if not project_dir:
        return ""
    return os.path.join(project_dir, ".memsearch", "memory")


def _collection(cfg: dict, project_dir: str) -> str:
    c = (_mem(cfg).get("collection") or "")
    if c:
        return str(c)
    if not project_dir:
        return "aichat_default"
    h = hashlib.sha1(os.path.normcase(os.path.abspath(project_dir)).encode("utf-8")).hexdigest()
    return "aichat_" + h[:8]


def _milvus_uri(cfg: dict) -> str:
    u = (_mem(cfg).get("milvus_uri") or "~/.memsearch/milvus.db")
    return os.path.expandvars(os.path.expanduser(str(u)))


def _run_cli(cmd: list, cwd: str, timeout: float | None):
    env = {**os.environ, "PYTHONUTF8": "1"}
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(
        cmd, cwd=cwd or None, env=env, encoding="utf-8", errors="replace",
        capture_output=True, timeout=timeout, creationflags=creationflags,
    )


def _run_cli_locked(cmd: list, cwd: str, timeout: float | None, tag: str):
    """进程内串行调用 memsearch；锁冲突则释放锁后重试（重试期间不持锁）。

    返回 subprocess.CompletedProcess（returncode==0）或 None（降级为空）。
    """
    for attempt in range(_RETRY_MAX + 1):
        with _CLI_LOCK:
            try:
                cp = _run_cli(cmd, cwd, timeout)
            except subprocess.TimeoutExpired:
                _log(f"{tag} 超时（> {timeout}s）降级为空")
                return None
            except Exception as e:  # noqa: BLE001
                _log(f"{tag} 异常降级为空：{e}")
                return None
        if cp.returncode == 0:
            return cp
        output = (cp.stdout or "") + (cp.stderr or "")
        if _LOCK_RE.search(output):
            if attempt < _RETRY_MAX:
                _log(f"{tag} Milvus Lite 锁冲突，{_RETRY_SLEEP_S}s 后重试（{attempt + 1}/{_RETRY_MAX}）")
                time.sleep(_RETRY_SLEEP_S)  # 重试期间不持有 _CLI_LOCK
                continue
            _log(f"{tag} 锁冲突重试后仍失败，降级为空")
            return None
        _log(f"{tag} 子进程返回 {cp.returncode}，降级为空")
        return None
    return None


# ---------------- 召回 ----------------

def _memory_recall_core(cfg: dict, project_dir: str, query: str) -> str:
    """返回可直接拼进 prompt 的召回块；任何失败返回空字符串。"""
    mem = _mem(cfg)
    if not mem.get("enabled"):
        return ""
    cli = _cli_path(cfg)
    if not cli:
        _warn_once("memsearch CLI 未找到（memory.cli），召回降级为空")
        return ""
    if not project_dir or not os.path.isdir(project_dir):
        return ""
    source_dir = _source_dir(cfg, project_dir)
    if not os.path.isdir(source_dir):
        return ""  # 空/不存在的 source_dir → 空（只有 write_back 才创建目录）
    collection = _collection(cfg, project_dir)
    milvus_uri = _milvus_uri(cfg)
    top_k = int(mem.get("top_k", 4))
    max_chars = int(mem.get("max_chars", 1200))
    timeout = float(mem.get("search_timeout_s", 8))
    cmd = [cli, "search", query, "-k", str(top_k), "-j", "-c", collection,
           "--milvus-uri", str(milvus_uri)]
    cp = _run_cli_locked(cmd, project_dir, timeout, "memory_recall")
    if cp is None:
        return ""
    try:
        arr = json.loads(cp.stdout)
    except Exception:
        _log("memory_recall JSON 解析失败，降级为空")
        return ""
    if not isinstance(arr, list) or not arr:
        return ""
    try:
        arr.sort(key=lambda x: -(float(x.get("score") or 0) if isinstance(x, dict) else 0))
    except Exception:
        pass
    lines: list[str] = []
    total = 0
    seen: set[str] = set()
    for item in arr:
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        key = content[:64]
        if key in seen:
            continue
        seen.add(key)
        src = item.get("source") or ""
        heading = item.get("heading") or ""
        loc = (f"{src}#{heading}") if heading else src
        piece = content if len(content) <= max_chars else content[:max_chars] + "…"
        if total + len(piece) > max_chars and lines:
            room = max_chars - total
            if room > 40:
                piece = piece[:room] + "…"
            else:
                break
        lines.append(f"- [来源：{loc}] {piece}")
        total += len(piece)
        if total >= max_chars:
            break
    if not lines:
        return ""
    block = (
        "【长期记忆 · MemSearch 语义召回（可能过时，以当前会话与最新文件为准）】\n"
        + "\n".join(lines)
        + "\n\n（以上为历史记忆，仅供参考；与当前代码/数据冲突时以当前为准。）"
    )
    return block


# ---------------- 召回（对外入口：召回块 + 固定纪律，B 项 2026-11-13） ----------------

def memory_recall(cfg: dict, project_dir: str, query: str) -> str:
    """召回块 + memory.rules_text 固定纪律；任一段为空则只用另一段；全空/关闭返回空串。"""
    mem = _mem(cfg)
    if not mem.get("enabled"):
        return ""
    rules = str(mem.get("rules_text") or "").strip()
    core = _memory_recall_core(cfg, project_dir, query)
    if not rules:
        return core
    fixed = "【记忆三层固定纪律（平台配置，长期有效）】\n" + rules
    return (core + "\n\n" + fixed) if core else fixed


# ---------------- 写回 ----------------

def _redact_linewise(text: str) -> str:
    if not text:
        return ""
    out: list[str] = []
    for ln in str(text).splitlines():
        low = ln.lower()
        if any(k in low for k in ("key", "token", "密钥", "密码", "password", "secret",
                                  "credential", "api_key", "bearer", "access_key")):
            out.append("（已脱敏：含密钥/Token 字样，不写回）")
            continue
        if _SECRET_RE.search(ln):
            out.append("（已脱敏：疑似密钥串，不写回）")
            continue
        out.append(ln)
    return "\n".join(out)


def memory_write(cfg: dict, project_dir: str, question: str, answer: str,
                 adapter: str, kind: str = "chat") -> None:
    """把本轮问答追加到 <source_dir>/YYYY-MM-DD.md；随后 debounce 触发后台索引。"""
    mem = _mem(cfg)
    if not mem.get("enabled"):
        return
    if not mem.get("write_back"):
        return
    if not project_dir:
        return
    source_dir = _source_dir(cfg, project_dir)
    try:
        os.makedirs(source_dir, exist_ok=True)
    except Exception:
        return
    now = datetime.now()
    date_file = now.strftime("%Y-%m-%d") + ".md"
    path = os.path.join(source_dir, date_file)
    q = _redact_linewise(question)
    if len(q) > 1000:
        q = q[:1000] + "…"
    a = _redact_linewise(answer)
    max_chars = int(mem.get("write_back_max_chars", 4000))
    if len(a) > max_chars:
        a = a[:max_chars] + "…"
    block = (
        f"### {now.strftime('%H:%M')} · {adapter} · {kind}\n"
        f"<!-- session:aichat-{project_dir} -->\n"
        f"- 用户问：{q}\n"
        f"- AI 答：{a}\n\n"
    )
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(block)
    except Exception:
        return
    _schedule_index(cfg, project_dir)


# ---------------- 索引生命周期（无常驻进程；进程内串行） ----------------

def _index_once(cfg: dict, project_dir: str) -> None:
    """执行一次 memsearch index；失败只写日志，不影响聊天。"""
    try:
        mem = _mem(cfg)
        cli = _cli_path(cfg)
        if not cli or not project_dir or not os.path.isdir(project_dir):
            return
        source_dir = _source_dir(cfg, project_dir)
        if not os.path.isdir(source_dir):
            return
        collection = _collection(cfg, project_dir)
        milvus_uri = _milvus_uri(cfg)
        cmd = [cli, "index", source_dir, "-c", collection, "--milvus-uri", str(milvus_uri)]
        cp = _run_cli_locked(cmd, project_dir, _INDEX_TIMEOUT, "memory_index")
        if cp is None:
            _log(f"memory_index 索引失败（只记日志，不影响聊天）：{project_dir}")
            return
        _log(f"memory_index 索引完成：{project_dir} → {collection}")
    except Exception as e:  # noqa: BLE001
        _log(f"memory_index 异常（只记日志，不影响聊天）：{e}")


def _schedule_index(cfg: dict, project_dir: str) -> None:
    """写回后的 debounce 索引：每项目合并，已有未触发的 Timer 就跳过。"""
    mem = _mem(cfg)
    if not mem.get("enabled") or not mem.get("watch"):
        return  # watch=false → 只读已有索引，不自动建
    if not project_dir:
        return
    try:
        delay = max(0.05, float(mem.get("watch_debounce_ms", 3000)) / 1000.0)
        key = os.path.normcase(os.path.abspath(project_dir))
        with _TIMER_LOCK:
            old = _INDEX_TIMERS.get(key)
            if old is not None and old.is_alive():
                return  # 合并：已有未触发的 Timer
            t = threading.Timer(delay, _index_once, args=(cfg, project_dir))
            t.daemon = True
            _INDEX_TIMERS[key] = t
            t.start()
    except Exception as e:  # noqa: BLE001
        _log(f"memory_write 索引排程失败（只记日志）：{e}")


def memory_ensure_index(cfg: dict, project_dir: str) -> None:
    """服务启动时调用：后台线程跑一次 index，绝不产生常驻 memsearch 子进程。"""
    mem = _mem(cfg)
    if not mem.get("enabled") or not mem.get("watch"):
        return
    if not project_dir or not os.path.isdir(project_dir):
        return
    source_dir = _source_dir(cfg, project_dir)
    if not os.path.isdir(source_dir):
        return
    if not _cli_path(cfg):
        return
    try:
        t = threading.Thread(target=_index_once, args=(cfg, project_dir),
                             daemon=True, name="mem-index-startup")
        with _THREAD_LOCK:
            _INDEX_THREADS.append(t)
        t.start()
        _log(f"memory_ensure_index 后台索引线程已启动：{project_dir}")
    except Exception as e:  # noqa: BLE001
        _log(f"memory_ensure_index 启动失败（只记日志）：{e}")


def memory_ensure_watch(cfg: dict, project_dir: str) -> None:
    """兼容旧调用名：语义已改为「后台跑一次索引」，不再启动常驻 watch。"""
    memory_ensure_index(cfg, project_dir)


def memory_close_all() -> None:
    """退出收尾：取消所有未触发的 debounce Timer（不打断正在跑的一次性 index）。"""
    with _TIMER_LOCK:
        for _key, t in list(_INDEX_TIMERS.items()):
            try:
                t.cancel()
            except Exception:
                pass
        _INDEX_TIMERS.clear()
    with _THREAD_LOCK:
        _INDEX_THREADS.clear()
