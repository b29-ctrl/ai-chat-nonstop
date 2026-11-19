#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI聊不停 · 真实自动派发器 M1（零第三方依赖，仅标准库）

唯一规范：协作/20261110_豆包_给CODEX_工单调度台_派发器M1_实现任务书.md §0–§13
执行者：WK（WorkBuddy / CodeBuddy），任务书内所有 CODEX 均按 WK 理解。

派生口径（零新增表、零结构改动）：
  attempts       = 1 + COUNT(events WHERE kind='dispatch_fail' AND actor='派发器')
  领取时刻       = MAX(events.at WHERE kind='dispatch_intent' AND actor='派发器')
  最近失败摘要   = 最近 2 条 dispatch_fail.detail
  回件文本       = chat_messages WHERE conversation_id='ticket:<id>' AND role='assistant' 最近 2 条
  退避截止       = 最后一次 dispatch_fail.at + retry_backoff_s * 2**(已失败次数-1)
  项目熔断       = 窗口内不同单号 dispatch_fail 数 >= 阈值 且窗口内 reply_seen 为 0
  心跳           = meta.dispatcher_last_beat

状态机里"人工签收态"只由主理人经 /signoff 写入；本文件不持有该状态常量、
不在任何路径写入它，也不写 projects.status（只 SELECT）。
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ---- §2.1 导入：照抄，不自创；__main__ 保护确保不会起 HTTP 服务 ----
ROOT = Path(__file__).resolve().parent
_SVC = ROOT / "服务.py"
_spec = importlib.util.spec_from_file_location("deck_service", _SVC)
服务 = importlib.util.module_from_spec(_spec)
sys.modules["deck_service"] = 服务
_spec.loader.exec_module(服务)

# ---- 技能候选模块（WB-SKILL-ROUTE-20261115）：加载失败也不阻塞派发 ----
技能 = None
_SKILL_LOAD_ERR = ""
try:
    _SKILL_FILE = ROOT / "技能.py"
    if _SKILL_FILE.exists():
        _s_spec = importlib.util.spec_from_file_location("deck_skill", _SKILL_FILE)
        技能 = importlib.util.module_from_spec(_s_spec)
        sys.modules["deck_skill"] = 技能
        _s_spec.loader.exec_module(技能)
except Exception as _e:                                  # noqa: BLE001
    _SKILL_LOAD_ERR = f"{type(_e).__name__}: {_e}"
    技能 = None

# ---- 常量（白名单常量复用 服务.py；不定义人工签收态）----
ACTOR = "派发器"
S_TODO = "待处理"
S_RUNNING = "派发中"
S_REPLIED = "已回件"
S_REVIEW = "待验"
S_FAILED = "失败"
NO_GATE = {None, "", "无", "-"}
DEFAULT_PROJECT = "proj-default"
PAUSED_STATES = {"paused", "stopped"}

DEFAULT_DISPATCHER = {
    "enabled": True,
    "interval_s": 5,
    "max_concurrent": 2,
    "max_concurrent_per_project": 2,
    "max_retries": 2,                 # 仅兼容保留，以 max_attempts_per_ticket 为准
    "max_attempts_per_ticket": 3,
    "retry_backoff_s": 10,
    "claim_timeout_s": 2100,          # C10：AI 写码类长任务默认给足（与 配置.json 对齐）
    "dispatch_timeout_s": 1800,       # C10
    "ticket_deadline_s": 14400,       # C10
    "no_progress_repeat": 2,
    "breaker_consecutive_fail": 3,
    "breaker_window_s": 300,
    "workspace_change_check": "warn",  # C3：回件称完成但工作区无变更 → warn(默认)/fail/off
    "same_project_serial": False,      # C4：同项目工单强制串行（并发冲突兜底开关）
    "reassign": {},                    # 真机 2.2③：失败自动改派，如 {"workbuddy": ["codex"]}
}
_INT_KEYS = tuple(k for k, v in DEFAULT_DISPATCHER.items() if isinstance(v, int) and k != "enabled")

# §7.1 失败判定：中文错误短语黑名单，禁止"以【开头"判失败
# C1：「退出码异常」为 process_chat 对非零退出码的前缀标记，避免"崩溃但打印了东西"被误判成功；
#     关键词后允许跟尾注（如 退出码异常 exit=1】）再闭合。
FAIL_RE = re.compile(
    r"^【[^】]{0,40}(超时|无输出|启动失败|未配置|未找到|不在项目目录|未支持|未知适配器|返回错误|退出码异常|command)[^】]{0,60}】"
)
QUOTA_RE = re.compile(r"(超时|timeout|额度|quota|余额|402)", re.I)
ATTEMPT_PREFIX_RE = re.compile(r"^attempt=\d+/\d+\s*\|\s*")

LOG = logging.getLogger("deck_dispatcher")


# ---------------- 基础工具 ----------------

def now_dt() -> datetime:
    return datetime.now().astimezone()


def parse_time(s):
    try:
        dt = datetime.fromisoformat(str(s or ""))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now_dt().tzinfo)
    return dt


def short(text, n: int = 300) -> str:
    """所有入库/入日志文本一律 sanitize + 截断。"""
    return 服务.sanitize(str(text or ""))[:n]


def is_real_hash(s) -> bool:
    """§7.7 只认真十六进制哈希；manual/demo/空一律走弱判重。"""
    return bool(s) and bool(re.fullmatch(r"[0-9a-fA-F]{16,64}", str(s)))


def backoff_s(dcfg, fails: int) -> int:
    return int(dcfg["retry_backoff_s"]) * (2 ** max(0, fails - 1))


def setup_logging(data_dir: Path):
    """§8.4 5MB×5 轮转 + 控制台；格式 时间 | 级别 | 单号 | 事件 | 摘要。"""
    LOG.setLevel(logging.INFO)
    LOG.handlers.clear()
    LOG.propagate = False
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    data_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(str(data_dir / "派发器.log"), maxBytes=5 * 1024 * 1024,
                             backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    LOG.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    LOG.addHandler(sh)


def log(level, tid, event, summary=""):
    LOG.log(level, "%s | %s | %s", tid or "-", event, short(summary, 300))


def ro():
    """只读连接：用于探活 / dry-run / 预检，绝不对台账设置 journal_mode。"""
    uri = Path(服务.DB_PATH).resolve().as_uri() + "?mode=ro"
    c = sqlite3.connect(uri, uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def rw():
    return 服务.db()


def probe_tickets_table() -> bool:
    """§2.4 只读探活；缺表 → 退出码 3。绝不调用 init_db() 于真实库。"""
    try:
        c = ro()
        r = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tickets'").fetchone()
        c.close()
        return bool(r)
    except Exception:
        return False


def dispatcher_cfg(cfg: dict) -> dict:
    d = dict(DEFAULT_DISPATCHER)
    raw = cfg.get("dispatcher") or {}
    if isinstance(raw, dict):
        d.update({k: v for k, v in raw.items() if k in d})
    for k in _INT_KEYS:
        try:
            d[k] = max(0, int(d[k]))
        except Exception:
            d[k] = DEFAULT_DISPATCHER[k]
    if isinstance(d["enabled"], str):
        d["enabled"] = d["enabled"].strip().lower() not in ("false", "0", "no", "off", "")
    d["enabled"] = bool(d["enabled"])
    d["max_concurrent"] = max(1, d["max_concurrent"])
    d["max_concurrent_per_project"] = max(1, d["max_concurrent_per_project"])
    d["max_attempts_per_ticket"] = max(1, d["max_attempts_per_ticket"])
    # C3/C4/改派：非整型配置项校验
    if str(d.get("workspace_change_check", "")).strip().lower() not in ("warn", "fail", "off"):
        d["workspace_change_check"] = "warn"
    else:
        d["workspace_change_check"] = str(d["workspace_change_check"]).strip().lower()
    d["same_project_serial"] = bool(d.get("same_project_serial"))
    if not isinstance(d.get("reassign"), dict):
        d["reassign"] = {}
    else:
        d["reassign"] = {str(k).strip().lower(): [str(x).strip() for x in (v or []) if str(x).strip()]
                         for k, v in d["reassign"].items() if isinstance(v, list)}
    return d


def classify(reply) -> tuple[bool, str]:
    """§7.1 判定顺序：空 → 人工席位 → 黑名单 → 其余成功。"""
    s = 服务.sanitize(reply or "")
    if not s.strip():
        return False, "empty_reply"
    if s.startswith("【大BOSS】") or "人工中转岗" in s[:120]:
        return False, "human_seat"
    if FAIL_RE.match(s):
        return False, "adapter_error"
    return True, ""


def pid_alive(pid: int) -> bool:
    """§6.2 Windows 用 OpenProcess/GetExitCodeProcess；POSIX 用 kill(pid,0)。"""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        except Exception:
            try:
                import subprocess
                r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=8)
                return str(pid) in (r.stdout or "")
            except Exception:
                return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


# ---------------- 运行上下文 ----------------

class Ctx:
    def __init__(self, args, cfg, dcfg, data_dir, lock_path, pid_path):
        self.args = args
        self.base_cfg = cfg
        self.dcfg = dcfg
        self.data_dir = data_dir
        self.lock_path = lock_path
        self.pid_path = pid_path
        self.stop = threading.Event()
        self.self_claimed = set()
        self.inflight = {}              # future -> project_id
        self.alert_dedup = {}           # (pid, tid, reason_key) -> epoch
        self.holds_lock = False
        self.pool = ThreadPoolExecutor(max_workers=dcfg["max_concurrent"])

    def reap(self):
        for f in [x for x in list(self.inflight) if x.done()]:
            self.inflight.pop(f, None)
            try:
                f.result()
            except Exception as e:                       # noqa: BLE001
                log(logging.ERROR, "-", "worker_future_error", e)

    def project_active(self, pid) -> int:
        return sum(1 for p in self.inflight.values() if p == pid)


def alert(ctx, pid, reason, tid=None, reason_key=None):
    """§8.3 直调 服务.notify_boss()，不调 HTTP；同单同原因 30s 去重。"""
    pid = pid or DEFAULT_PROJECT
    key = (pid, tid or "", reason_key or reason)
    now = time.time()
    if now - ctx.alert_dedup.get(key, 0) < 30:
        return
    ctx.alert_dedup[key] = now
    try:
        服务.notify_boss(pid, short(reason, 300), tid)
        log(logging.INFO, tid, "alert", reason)
    except Exception as e:                                # noqa: BLE001
        log(logging.WARNING, tid, "alert_failed", e)


# ---------------- 心跳 / 恢复 / 熔断 ----------------

def beat(ctx):
    if ctx.args.dry_run:
        return
    conn = rw()
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('dispatcher_last_beat',?)",
                         (服务.now_iso(),))
    finally:
        conn.close()


def recover_claims(ctx) -> set:
    """§6.4 硬杀后的领取回收：只认 actor=派发器 的 dispatch_intent，且本进程未持有。
    C1：intent 带有领取进程 pid（新版本）；pid 已不存活 → 立即回收，不等 claim_timeout；
    旧记录无 pid 时回落 claim_timeout 判定。"""
    conn = rw()
    recovered = set()
    try:
        rows = conn.execute("SELECT id FROM tickets WHERE status=?", (S_RUNNING,)).fetchall()
        cutoff = now_dt() - timedelta(seconds=ctx.dcfg["claim_timeout_s"])
        for r in rows:
            tid = r["id"]
            if tid in ctx.self_claimed:
                continue
            ev = conn.execute(
                "SELECT at, detail FROM events WHERE ticket_id=? AND kind='dispatch_intent' AND actor=? "
                "ORDER BY id DESC LIMIT 1", (tid, ACTOR)).fetchone()
            at = parse_time(ev["at"] if ev else None)
            if at is None:
                continue
            claim_pid = 0
            m = re.search(r"pid=(\d+)", ev["detail"] or "") if ev else None
            if m:
                try:
                    claim_pid = int(m.group(1))
                except Exception:
                    claim_pid = 0
            if claim_pid and claim_pid != os.getpid() and not pid_alive(claim_pid):
                why = f"领取进程已退出（pid={claim_pid} 不存活），立即回收为待处理（C1）"
            elif at > cutoff:
                continue
            else:
                why = f"领取超时（>{ctx.dcfg['claim_timeout_s']}s）且本进程未持有，回收为待处理"
            with conn:
                cur = conn.execute(
                    "UPDATE tickets SET status=?, updated_at=? WHERE id=? AND status=?",
                    (S_TODO, 服务.now_iso(), tid, S_RUNNING))
                if cur.rowcount == 1:
                    服务.add_event(conn, tid, "claim_recovered", ACTOR, short(why, 300))
                    recovered.add(tid)
                    log(logging.WARNING, tid, "claim_recovered", why)
    finally:
        conn.close()
    return recovered


def recompute_breakers(ctx, persist=True) -> set:
    """§7.5 熔断：窗口内不同单号失败数达阈值且窗口内无 reply_seen。只 SELECT projects。"""
    conn = ro()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT e.ticket_id,e.kind,e.at,t.project_id FROM events e "
            "LEFT JOIN tickets t ON t.id=e.ticket_id "
            "WHERE e.kind IN ('dispatch_fail','reply_seen') AND e.actor=?", (ACTOR,)).fetchall()]
    finally:
        conn.close()
    cutoff = now_dt() - timedelta(seconds=ctx.dcfg["breaker_window_s"])
    fails, replied = {}, set()
    for r in rows:
        at = parse_time(r["at"])
        if not at or at < cutoff:
            continue
        pid = r["project_id"] or DEFAULT_PROJECT
        if r["kind"] == "dispatch_fail":
            fails.setdefault(pid, set()).add(r["ticket_id"])
        else:
            replied.add(pid)
    out = set()
    for pid, ids in fails.items():
        if len(ids) < ctx.dcfg["breaker_consecutive_fail"] or pid in replied:
            continue
        out.add(pid)
        if not persist:
            continue
        conn = rw()
        try:
            key = "breaker_notified:" + pid
            row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
            last = parse_time(row["v"] if row else None)
            if last and (now_dt() - last).total_seconds() < ctx.dcfg["breaker_window_s"]:
                continue
            with conn:
                conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (key, 服务.now_iso()))
                服务.add_event(conn, sorted(ids)[-1], "breaker", ACTOR,
                               short(f"连续 {len(ids)} 张失败，暂停领单，等人工", 300))
        finally:
            conn.close()
        alert(ctx, pid, f"连续 {len(ids)} 张派单失败已熔断，请判断（项目 {pid}）",
              reason_key="breaker")
        log(logging.WARNING, pid, "breaker", f"连续 {len(ids)} 张失败，暂停领单")
    return out


# ---------------- 候选与预检 ----------------

def query_candidates(ctx, excluded: set) -> list:
    sql = "SELECT * FROM tickets WHERE status=?"
    params = [S_TODO]
    if ctx.args.ticket:
        sql += " AND id=?"
        params.append(ctx.args.ticket)
    if ctx.args.project:
        sql += " AND project_id=?"
        params.append(ctx.args.project)
    sql += " ORDER BY created_at, id"
    conn = ro()
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return [r for r in rows if r["id"] not in excluded]


def duplicate_reason(t, all_tickets, remaining_ids):
    """§7.7 真哈希 / 弱判重；排除本轮尚未预检的候选，保证同组只放第一张过。"""
    pid = t["project_id"] or DEFAULT_PROJECT
    h = t["canonical_sha256"] or ""
    real = is_real_hash(h)
    states = {S_TODO, S_RUNNING, S_REPLIED, S_REVIEW} if real else {S_TODO, S_RUNNING}
    for o in all_tickets:
        if o["id"] == t["id"] or o["id"] in remaining_ids:
            continue
        if o["status"] not in states:
            continue
        if real:
            if (o["canonical_sha256"] or "") == h:
                return f"同哈希已有单 {o['id']}"
        else:
            if ((o["project_id"] or DEFAULT_PROJECT) == pid
                    and (o["executor"] or "") == (t["executor"] or "")
                    and (o["title"] or "") == (t["title"] or "")):
                return f"同项目/执行端/标题已有单 {o['id']}"
    return None


def fail_count(conn, tid) -> int:
    r = conn.execute(
        "SELECT COUNT(*) AS c FROM events WHERE ticket_id=? AND kind='dispatch_fail' AND actor=?",
        (tid, ACTOR)).fetchone()
    return int(r["c"] if r else 0)


def precheck(ctx, conn, t, all_tickets, remaining_ids, breakers):
    """§7.6 每个候选都过；只返回跳过原因，除单张时限外不写任何表。"""
    tid = t["id"]
    pid = t["project_id"] or DEFAULT_PROJECT
    proj = conn.execute("SELECT id,name,dir,status FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj:
        return "项目不存在"
    pstate = (proj["status"] or "running")
    if pstate in PAUSED_STATES:
        return f"项目已 {pstate}，静默跳过"
    if pid in breakers:
        return "项目熔断中，暂停领单"
    executor = (t["executor"] or "").strip()
    if not executor:
        return "未指定执行端"
    ad = 服务.find_adapter(ctx.base_cfg, executor)
    if ad and (ad.get("type") or "").lower() == "human":
        return "人工席位，不派"

    created = parse_time(t["created_at"])
    if (created and ctx.dcfg["ticket_deadline_s"] > 0
            and (now_dt() - created).total_seconds() > ctx.dcfg["ticket_deadline_s"]):
        if not ctx.args.dry_run:
            c2 = rw()
            try:
                with c2:
                    cur = c2.execute(
                        "UPDATE tickets SET status=?, next_action=?, updated_at=? "
                        "WHERE id=? AND status=?",
                        (S_FAILED, "超单张时限，等待人工", 服务.now_iso(), tid, S_TODO))
                    if cur.rowcount == 1:
                        服务.add_event(c2, tid, "loop_guard_tripped", ACTOR,
                                       short("reason=deadline_exceeded | 超单张时限，等待人工", 300))
            finally:
                c2.close()
            alert(ctx, pid, f"单号 {tid} 超单张时限，等待人工", tid, "deadline")
        return "超单张时限"

    n = fail_count(conn, tid)
    if n >= ctx.dcfg["max_attempts_per_ticket"]:
        return "重试已用尽，不再领取"
    last = conn.execute(
        "SELECT at FROM events WHERE ticket_id=? AND kind='dispatch_fail' AND actor=? "
        "ORDER BY id DESC LIMIT 1", (tid, ACTOR)).fetchone()
    if last:
        b = backoff_s(ctx.dcfg, n)
        at = parse_time(last["at"])
        if at and (now_dt() - at).total_seconds() < b:
            return f"退避未到点（{b}s）"

    dup = duplicate_reason(t, all_tickets, remaining_ids)
    if dup:
        return dup
    return None


# ---------------- 领取 / 成功 / 失败 ----------------

def claim(ctx, t, attempt: int) -> bool:
    """§5 单条条件更新 + 状态与事件同一事务；领取只发生在主线程。"""
    conn = rw()
    try:
        with conn:
            cur = conn.execute(
                "UPDATE tickets SET status=?, updated_at=? WHERE id=? AND status=?",
                (S_RUNNING, 服务.now_iso(), t["id"], S_TODO))
            if cur.rowcount != 1:
                return False
            服务.add_event(conn, t["id"], "dispatch_intent", ACTOR,
                           short(f"派发器领取：{t['executor']} 第 {attempt} 次 pid={os.getpid()}", 300))
        ctx.self_claimed.add(t["id"])
        log(logging.INFO, t["id"], "dispatch_intent", f"领取成功，第 {attempt} 次")
        return True
    finally:
        conn.close()


def handle_success(ctx, t, reply, pid):
    """§8.2 同一事务：状态 + chat_messages 回件 + reply_seen 摘要。"""
    clean = 服务.sanitize(reply or "")
    tid = t["id"]
    gate = (t["gate"] or "").strip()
    if gate in NO_GATE:
        status, nxt = S_REPLIED, "已回件，无门禁"
    else:
        status, nxt = S_REVIEW, f"{gate} 待评审，等待主理人签收"
    lines = clean.splitlines()
    first = lines[0] if lines else ""
    summary = short(f"{first[:120]} | 共 {len(clean)} 字，已写入 ticket:{tid}", 300)
    now = 服务.now_iso()
    conn = rw()
    try:
        with conn:
            cur = conn.execute(
                "UPDATE tickets SET status=?, next_action=?, updated_at=? WHERE id=? AND status=?",
                (status, nxt, now, tid, S_RUNNING))
            if cur.rowcount != 1:
                log(logging.WARNING, tid, "stale_after_claim", "状态已被外部改动，只 warn，不写表不告警")
                return
            cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
            if "project_id" in cols:
                conn.execute(
                    "INSERT INTO chat_messages(conversation_id,role,content,adapter,model,ticket_id,project_id,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (f"ticket:{tid}", "assistant", clean, t["executor"], t["model"], tid, pid, now))
            else:
                # 老库未跑过轻量迁移：省略该列，绝不自行 ALTER
                conn.execute(
                    "INSERT INTO chat_messages(conversation_id,role,content,adapter,model,ticket_id,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (f"ticket:{tid}", "assistant", clean, t["executor"], t["model"], tid, now))
            服务.add_event(conn, tid, "reply_seen", ACTOR, summary)
    finally:
        conn.close()
    log(logging.INFO, tid, "reply_seen", f"成功 → {status}")
    if status == S_REVIEW:
        alert(ctx, pid, f"单号 {tid} 已进入门禁待评审，请主理人签收", tid, "gate_review")


def handle_failure(ctx, t, attempt, error_phrase, pid, *, terminal=False,
                   next_action=None, alert_reason=None):
    """§7.3/§7.4 失败：dispatch_fail + 退避/终态 + 无进展 + 告警，全部有上限。"""
    tid = t["id"]
    m = ctx.dcfg["max_attempts_per_ticket"]
    phrase = short(error_phrase, 80) or "未知错误"
    detail = short(f"attempt={attempt}/{m} | {phrase}", 300)
    reason = None
    # C1：失败原因（超时/无输出/退出码异常等）直接进 next_action，工单列表即可见，不用翻事件
    status, nxt = S_TODO, f"派发失败（{phrase[:60]}），第 {attempt} 次，退避 {backoff_s(ctx.dcfg, attempt)}s 后重试"
    conn = rw()
    try:
        with conn:
            cur = conn.execute("SELECT status FROM tickets WHERE id=?", (tid,)).fetchone()
            if not cur or cur["status"] != S_RUNNING:
                log(logging.WARNING, tid, "stale_after_claim", "急停/人工已改动状态，只 warn，不写表不告警")
                return
            服务.add_event(conn, tid, "dispatch_fail", ACTOR, detail)
            if terminal:
                status = S_FAILED
                nxt = next_action or "派发前置条件不满足，等待人工"
            else:
                # §7.4 先判回件 hash（M2 结构化返回后生效），再判错误摘要
                reps = conn.execute(
                    "SELECT content FROM chat_messages WHERE conversation_id=? AND role='assistant' "
                    "ORDER BY id DESC LIMIT 2", (f"ticket:{tid}",)).fetchall()
                if len(reps) >= 2:
                    hs = [hashlib.sha256((r["content"] or "").strip().encode("utf-8")).hexdigest()[:16]
                          for r in reps]
                    if hs[0] == hs[1]:
                        reason = "no_progress_same_reply"
                if reason is None:
                    recent = conn.execute(
                        "SELECT detail FROM events WHERE ticket_id=? AND kind='dispatch_fail' AND actor=? "
                        "ORDER BY id DESC LIMIT ?", (tid, ACTOR, ctx.dcfg["no_progress_repeat"])).fetchall()
                    if len(recent) >= 2:
                        norm = [服务.sanitize(ATTEMPT_PREFIX_RE.sub("", r["detail"] or ""))[:60]
                                for r in recent]
                        if norm[0].strip() and norm[0] == norm[1]:
                            reason = "no_progress_same_error"
                if reason is None and attempt >= m:
                    reason = "attempts_exhausted"
                if reason:
                    status = S_FAILED
                    nxt = {"attempts_exhausted": "重试已用尽，等待人工处理",
                           "no_progress_same_error": "无进展：连续相同错误，等待人工复核",
                           "no_progress_same_reply": "无进展：连续相同回件，等待人工复核"}[reason]
                else:
                    # 真机 2.2③：非终态失败且配置了改派链 → 同事务自动换执行端（模型清空走新端默认）
                    alt = next_reassign(ctx, t)
                    if alt:
                        conn.execute(
                            "UPDATE tickets SET executor=?, model=NULL, updated_at=? WHERE id=? AND status=?",
                            (alt, 服务.now_iso(), tid, S_RUNNING))
                        服务.add_event(conn, tid, "auto_reassign", ACTOR,
                                       short(f"{t['executor']} 派发失败，自动改派 → {alt}", 300))
                        log(logging.WARNING, tid, "auto_reassign", f"{t['executor']} → {alt}")
            conn.execute(
                "UPDATE tickets SET status=?, next_action=?, updated_at=? WHERE id=? AND status=?",
                (status, nxt, 服务.now_iso(), tid, S_RUNNING))
            if reason:
                服务.add_event(conn, tid, "loop_guard_tripped", ACTOR,
                               short(f"reason={reason} | {nxt}", 300))
    finally:
        conn.close()
    log(logging.WARNING, tid, "dispatch_fail", f"{status} | {detail}")
    why = alert_reason or phrase
    if reason:
        why = f"单号 {tid} {reason}：{nxt}"
    elif terminal:
        why = f"单号 {tid} {why}，请判断"
    elif QUOTA_RE.search(phrase):
        why = f"单号 {tid} 超时/额度类错误：{phrase}"
    else:
        why = None
    if why:
        alert(ctx, pid, why, tid, reason or (alert_reason and "precheck") or "failure")


# ---------------- 提示词 / worker ----------------

def next_reassign(ctx, t):
    """真机 2.2③：按 配置.json dispatcher.reassign 改派链取下一个可用执行端。"""
    m = ctx.dcfg.get("reassign") or {}
    alts = m.get((t["executor"] or "").strip().lower()) or []
    for a in alts:
        a = str(a).strip()
        if not a or a.lower() == (t["executor"] or "").strip().lower():
            continue
        ad = 服务.find_adapter(ctx.base_cfg, a)
        if ad and 服务.is_enabled(ad) and (ad.get("type") or "").lower() not in ("human", "pull"):
            return a
    return None


# C3：工作区快照——排除重目录/产物目录，超上限放弃检测（宁可不判，不误判）
_SNAP_EXCLUDE = {"node_modules", ".git", "dist", "build", "out", "coverage", "__pycache__",
                 ".memsearch", ".venv", "venv", ".next", "数据", "_验收截图", "验收材料"}
_SNAP_MAX_FILES = 5000


def workspace_snapshot(pdir: str):
    """返回 {相对路径: (大小, mtime秒)}；目录不存在/超大项目返回 None（跳过检测）。"""
    try:
        root = Path(服务.resolve_path(pdir or "")).resolve()
    except Exception:                                    # noqa: BLE001
        return None
    if not pdir or not root.is_dir():
        return None
    items = {}

    def walk(d: Path, rel: str):
        if len(items) > _SNAP_MAX_FILES:
            return
        try:
            entries = sorted(d.iterdir())
        except Exception:                                # noqa: BLE001
            return
        for e in entries:
            if len(items) > _SNAP_MAX_FILES:
                return
            if e.is_dir():
                if e.name.casefold() in _SNAP_EXCLUDE or e.name.startswith("."):
                    continue
                walk(e, f"{rel}{e.name}/")
            elif e.is_file():
                try:
                    st = e.stat()
                    items[f"{rel}{e.name}"] = (st.st_size, int(st.st_mtime))
                except OSError:
                    pass

    walk(root, "")
    if len(items) > _SNAP_MAX_FILES:
        return None
    return items


def same_project_running_hint(ctx, tid: str, pid: str) -> bool:
    """C4：同项目是否还有其他「派发中」工单（用于派单提示只追加不重写）。"""
    conn = ro()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM tickets WHERE project_id=? AND status=? AND id<>?",
            (pid, S_RUNNING, tid)).fetchone()
        return int(n["c"] if n else 0) > 0
    except Exception:                                    # noqa: BLE001
        return False
    finally:
        conn.close()


def tripcard_block(t, cfg) -> str:
    """WB-SKILL-ROUTE-20261115 R6：标题/备注/下一步命中出门条关键词时追加固定段。
    tripcard.enabled=false 或 path 为空 → 整段不出现；文件不存在仍注入并 warn；异常降级不阻塞派单。"""
    try:
        s_cfg = (cfg or {}).get("skills") or {}
        if not isinstance(s_cfg, dict):
            return ""
        tc = s_cfg.get("tripcard") or {}
        if not isinstance(tc, dict):
            return ""

        def truthy(v):
            return str(v).strip().lower() not in ("false", "0", "no", "off", "")

        if not truthy(tc.get("enabled", True)):
            return ""
        path = str(tc.get("path") or "").strip()
        if not path:
            return ""
        kws = tc.get("keywords") or []
        if isinstance(kws, str):
            kws = [kws]
        q = f"{t['title'] or ''} {t['note'] or ''} {t['next_action'] or ''}".lower()
        if not any(str(k).strip().lower() in q for k in kws if str(k).strip()):
            return ""
        if not os.path.isfile(path):
            log(logging.WARNING, t["id"], "tripcard_file_missing", path)
        return (
            "【本单要出门条】本单涉及出图/一页讲清。出图前先读 " + path +
            " 并填完六格（一句话问题 / 版本 / 每个框的出处 / 三个自查 / 未决 / 检查到哪一步）；"
            "填不满先补齐再画。回件末尾自答三问：①每个框有出处吗 ②有没有把“只是配了”画成“在跑” "
            "③有没有人真的用眼睛看过图。"
        )
    except Exception as _e:                              # noqa: BLE001
        log(logging.WARNING, t["id"], "tripcard_block_degraded", f"{type(_e).__name__}: {_e}")
        return ""


def build_prompt(t, proj, cfg=None) -> str:
    """§8.1 逐字段拼；不塞配置、不塞 Key、不塞 00_环境.md 全文。
    WB-SKILL-ROUTE-20261115：在「要求：」前插入技能候选块；候选块异常一律降级，不阻塞派单。"""
    pdir = ((proj["dir"] if proj else "") or "未设置")
    # 技能候选块：任何异常都只降级为未命中段（技能模块自身也做同样保证）
    skill_block = ""
    try:
        if 技能 is not None:
            q = f"{t['title'] or ''} {t['note'] or ''} {t['next_action'] or ''}"
            # R8：把当前工单执行端（小写）传给候选块做岗位过滤；空串=不过滤（兼容旧行为）
            executor = str(t.get("executor") or "").strip().lower()
            skill_block = 技能.候选块(q, cfg=cfg, role=executor)
        elif _SKILL_LOAD_ERR:
            log(logging.WARNING, t["id"], "skill_module_unavailable", _SKILL_LOAD_ERR)
    except Exception as _e:                              # noqa: BLE001
        log(logging.WARNING, t["id"], "skill_block_degraded", f"{type(_e).__name__}: {_e}")
        skill_block = "【可用技能候选】本单未命中任何技能，按常规执行。"
    # R6 出门条块：在技能候选块之后；任何异常只降级为空，不阻塞派单
    trip_block = tripcard_block(t, cfg)
    parts = [b for b in (skill_block, trip_block) if b]
    prefix = ("\n\n".join(parts) + "\n\n") if parts else ""
    return (
        f"【工单派发】单号 {t['id']}\n"
        f"标题：{t['title'] or ''}\n"
        f"项目：{(proj['name'] if proj else '') or DEFAULT_PROJECT}（目录：{pdir}）\n"
        f"执行端：{t['executor'] or ''}    模型：{t['model'] or '未指定'}\n"
        f"门：{t['gate'] or '无'}    优先级：{t['priority'] or ''}\n"
        f"下一步：{t['next_action'] or ''}\n"
        f"备注：{t['note'] or '无'}\n\n"
        + prefix +
        "要求：\n"
        "1) 只做本单范围，不扩大、不顺带修别的问题；\n"
        "2) 若备注里点了 S4/任务文件，先读该文件再动手；\n"
        f"3) 回件开头必须带单号 {t['id']}，末尾写“改了什么 / 怎么验的 / 还差什么 / 用到技能：<技能name 或 无>”；\n"
        "4) 不要改门禁、不要签收、不要声称已发版；门禁由主理人签；\n"
        "5) 需要拍板 / 超时 / 额度不足 → 停下说明，不要自行越过。"
    )


def skill_hit_detail(cfg, t) -> str:
    """WB-SKILL-ROUTE-20261115：算本单 skill_candidates 事件 detail。
    技能关闭/日志关闭/异常 → 返回 ''（不落事件，不阻塞派单）。"""
    if 技能 is None:
        return ""
    try:
        s_cfg = cfg.get("skills") or {}
        if not isinstance(s_cfg, dict):
            s_cfg = {}

        def truthy(v):
            return str(v).strip().lower() not in ("false", "0", "no", "off", "")

        if not truthy(s_cfg.get("enabled", True)) or not truthy(s_cfg.get("log_event", True)):
            return ""
        q = f"{t['title'] or ''} {t['note'] or ''} {t['next_action'] or ''}"
        names = [m["name"] for m in 技能.match(q, cfg=cfg, top_k=s_cfg.get("top_k", 3))]
        return ",".join(names) if names else "none"
    except Exception as _e:                              # noqa: BLE001
        log(logging.WARNING, t["id"], "skill_event_degraded", f"{type(_e).__name__}: {_e}")
        return ""


def write_skill_event(tid, detail):
    """落 skill_candidates 事件；写库失败只 warn，不影响派单结果。"""
    try:
        conn = rw()
        try:
            with conn:
                服务.add_event(conn, tid, "skill_candidates", ACTOR, detail)
        finally:
            conn.close()
    except Exception as _e:                              # noqa: BLE001
        log(logging.WARNING, tid, "skill_event_write_fail", f"{type(_e).__name__}: {_e}")


def prepare_cfg(ctx, t, pdir):
    """§7.2/§7.6 每单浅拷贝 cfg；覆盖 timeout_s 只改内存，不 save_config。"""
    base = ctx.base_cfg
    cfg = dict(base)
    cfg["paths"] = dict(base.get("paths") or {})
    cfg["adapters"] = {k: dict(v) for k, v in (base.get("adapters") or {}).items()}
    cfg["security"] = dict(base.get("security") or {})
    if pdir:
        cfg["paths"]["project"] = 服务.resolve_path(pdir)
    executor = (t["executor"] or "").strip()
    aid = next((k for k in cfg["adapters"] if k.lower() == executor.lower()), None)
    if aid:
        cfg["adapters"][aid]["timeout_s"] = ctx.dcfg["dispatch_timeout_s"]
    return cfg, aid


def write_dispatch_ok(tid, label):
    conn = rw()
    try:
        with conn:
            服务.add_event(conn, tid, "dispatch_ok", ACTOR, short(f"已投递：{label}", 300))
    finally:
        conn.close()


def worker(ctx, t, attempt, fails):
    tid = t["id"]
    pid = t["project_id"] or DEFAULT_PROJECT
    try:
        c = ro()
        row = c.execute("SELECT status FROM tickets WHERE id=?", (tid,)).fetchone()
        proj = c.execute("SELECT id,name,dir,status FROM projects WHERE id=?", (pid,)).fetchone()
        c.close()
        if not row or row["status"] != S_RUNNING:
            log(logging.WARNING, tid, "stale_after_claim", "急停/人工已改动状态，只 warn，不写表不告警")
            return

        pdir = ((proj["dir"] if proj else "") or "").strip()
        # §7.6 claim 后判失败：目录不存在（不静默跳过，否则永远不出队）
        if pdir and not Path(服务.resolve_path(pdir)).is_dir():
            handle_failure(ctx, t, attempt, "项目目录不存在", pid, terminal=True,
                           next_action="项目目录不存在，等待人工", alert_reason="项目目录不存在")
            return

        cfg, aid = prepare_cfg(ctx, t, pdir)
        ad = 服务.find_adapter(cfg, t["executor"] or "")
        statuses = 服务.adapter_status(cfg)
        st = statuses.get(aid, {}) if aid else {}
        if not ad or not 服务.is_enabled(ad) or not st.get("ready", False):
            handle_failure(ctx, t, attempt, "执行端未配置或未就绪", pid, terminal=True,
                           next_action="执行端未配置或未就绪", alert_reason="执行端未配置或未就绪")
            return
        if (ad.get("type") or "").lower() == "pull":
            handle_failure(ctx, t, attempt, "pull 型适配器不支持同步收件", pid, terminal=True,
                           next_action="pull 型适配器不支持同步收件", alert_reason="pull 型适配器不支持同步收件")
            return
        if (ad.get("type") or "").lower() == "human":
            handle_failure(ctx, t, attempt, "人工席位，不派", pid, terminal=True,
                           next_action="人工席位，不派", alert_reason="人工席位，不派")
            return

        label = ad.get("label") or (t["executor"] or "")
        write_dispatch_ok(tid, label)          # 调用 dispatch_chat 之前
        prompt = build_prompt(t, proj, cfg)
        # C2：适配器可自带 prompt_suffix（如 COZE 云端端「只吃内联内容」约定）
        suffix = str(ad.get("prompt_suffix") or "").strip()
        if suffix:
            prompt += "\n\n" + suffix
        # C4：同项目有并行工单时，提示执行端只追加不重写、写前重读
        if same_project_running_hint(ctx, tid, pid):
            prompt += ("\n\n【并发提示】同项目当前有其他工单同时在执行，可能与你的改动落在相同文件："
                       "只做本单最小必要修改、只追加不整文件重写；写盘前先重读目标文件最新内容。")
        # C3：派发前快照工作区（仅 process 型且未被 ad.workspace_check=false 关闭）
        mode = ctx.dcfg["workspace_change_check"]
        do_check = (mode != "off" and (ad.get("type") or "").lower() == "process"
                    and ad.get("workspace_check", True))
        proj_dir = str(cfg.get("paths", {}).get("project") or "")
        snap_before = workspace_snapshot(proj_dir) if do_check else None
        if do_check and snap_before is None:
            log(logging.INFO, tid, "workspace_check_skipped", "工作区不可快照（目录缺失或超上限），跳过假完成检测")
        reply = 服务.dispatch_chat(cfg, t["executor"] or "", prompt, t["model"] or None)
        ok, kind = classify(reply)
        if ok:
            no_change = snap_before is not None and workspace_snapshot(proj_dir) == snap_before
            if no_change and mode == "fail":
                # C3 fail 模式：无变更按失败处理（退避重试/无进展熔断兜底）
                handle_failure(ctx, t, attempt, "回件声称完成但工作区无变更（假完成嫌疑）", pid)
                return
            handle_success(ctx, t, reply, pid)
            # WB-SKILL-ROUTE-20261115：派发成功后落技能命中事件（关闭/异常时 detail='' 自动跳过）
            skill_detail = skill_hit_detail(cfg, t)
            if skill_detail:
                write_skill_event(tid, skill_detail)
            if no_change:
                # C3 warn 模式（默认）：仍收件，但落可疑事件 + 告警，请主理人复核
                conn = rw()
                try:
                    with conn:
                        服务.add_event(conn, tid, "suspect_no_change", ACTOR,
                                       short("回件声称完成，但工作区（排除 node_modules/.git 等）无任何文件变更，请主理人复核是否假完成", 300))
                finally:
                    conn.close()
                alert(ctx, pid, f"单号 {tid} 回件称完成但工作区无变更（假完成嫌疑），请复核", tid, "suspect_no_change")
                log(logging.WARNING, tid, "suspect_no_change", "回件成功但工作区无变更")
        else:
            phrase = 服务.sanitize(reply or "")[:80] or f"{kind}（回件为空或命中错误短语）"
            handle_failure(ctx, t, attempt, phrase, pid)
    except Exception as e:                                  # noqa: BLE001
        log(logging.ERROR, tid, "worker_exception", e)
        try:
            handle_failure(ctx, t, attempt, f"派发器异常：{e}", pid)
        except Exception as e2:                             # noqa: BLE001
            log(logging.ERROR, tid, "failure_write_error", e2)
    finally:
        ctx.self_claimed.discard(tid)


# ---------------- 轮次 ----------------

def dry_adapter_reason(cfg, t, statuses, status_error=None):
    """仅 dry-run 预检：补 ready / pull 判定，把「真跑会踩的雷」提前打印出来。
    不参与真实派发路径——真实路径仍是 claim 后判失败（§7.6），语义不变。
    R3：adapter_status 读失败时明确报「读不到适配器状态」，不逐单伪装成未就绪。"""
    executor = (t["executor"] or "").strip()
    ad = 服务.find_adapter(cfg, executor)
    if not ad:
        return "执行端未就绪（找不到适配器）"
    typ = (ad.get("type") or "").lower()
    if typ == "pull":
        return "pull 型不支持同步收件"
    if typ == "human":
        return "人工席位，不派"
    if status_error:
        return f"读不到适配器状态（{status_error}）"
    aid = next((k for k in (cfg.get("adapters") or {}) if k.lower() == executor.lower()), None)
    st = statuses.get(aid, {}) if aid else {}
    if not 服务.is_enabled(ad) or not st.get("ready", False):
        return "执行端未就绪"
    return None


def dry_dir_reason(conn, t):
    """仅 dry-run（R1）：projects.dir 非空但不存在 → 提前报「项目目录不存在」。
    dir 为空按 §7.6 回落 cfg.paths.project，不算错，故不检查；真跑语义不变（#10）。"""
    pid = t["project_id"] or DEFAULT_PROJECT
    prow = conn.execute("SELECT dir FROM projects WHERE id=?", (pid,)).fetchone()
    pdir = ((prow["dir"] if prow else "") or "").strip()
    if pdir and not Path(服务.resolve_path(pdir)).is_dir():
        return "项目目录不存在"
    return None


def dry_print(ctx, breakers):
    conn = ro()
    try:
        candidates = query_candidates(ctx, set())
        all_tickets = [dict(r) for r in conn.execute(
            "SELECT id,status,title,executor,project_id,canonical_sha256 FROM tickets").fetchall()]
        remaining = {r["id"] for r in candidates}
        status_error = None
        try:
            statuses = 服务.adapter_status(ctx.base_cfg)   # 只读配置/文件，不写任何表
        except Exception as e:                              # noqa: BLE001
            statuses = {}
            status_error = 服务.sanitize(str(e)).replace("|", "/")[:120] or "未知异常"
        print(f"[dry-run] 候选 {len(candidates)} 张；熔断项目 {sorted(breakers) or '无'}")
        if status_error:
            print(f"[dry-run] 警告：读不到适配器状态（{status_error}），ready 判定不可用")
        for t in candidates:
            reason = precheck(ctx, conn, t, all_tickets, remaining, breakers)
            if reason is None:
                reason = dry_dir_reason(conn, t)
            if reason is None:
                reason = dry_adapter_reason(ctx.base_cfg, t, statuses, status_error)
            remaining.discard(t["id"])
            print(f"[dry-run] {t['id']} | {t['title']} | "
                  f"{'可派' if reason is None else '跳过：' + reason}")
    finally:
        conn.close()


def run_round(ctx):
    if ctx.args.dry_run:
        dry_print(ctx, recompute_breakers(ctx, persist=False))   # 只读，不写心跳/事件/告警
        return
    beat(ctx)                                                    # §6.3 每轮开始都写
    recovered = recover_claims(ctx)
    breakers = recompute_breakers(ctx, persist=True)
    candidates = query_candidates(ctx, recovered)
    conn = ro()
    try:
        all_tickets = [dict(r) for r in conn.execute(
            "SELECT id,status,title,executor,project_id,canonical_sha256 FROM tickets").fetchall()]
    finally:
        conn.close()
    conn = ro()
    try:
        remaining = {r["id"] for r in candidates}
        eligible = []
        for t in candidates:
            reason = precheck(ctx, conn, t, all_tickets, remaining, breakers)
            remaining.discard(t["id"])
            if reason:
                log(logging.INFO, t["id"], "skip", reason)
            else:
                eligible.append(t)
    finally:
        conn.close()

    pending = list(eligible)
    guard = 0
    max_guard = max(8, len(eligible) * 4 + 8)
    while pending and not ctx.stop.is_set() and guard < max_guard:
        guard += 1
        ctx.reap()
        for t in list(pending):
            ctx.reap()
            if len(ctx.inflight) >= ctx.dcfg["max_concurrent"]:
                break
            pid = t["project_id"] or DEFAULT_PROJECT
            # C4：same_project_serial=true 时同项目强制串行（并发文件冲突兜底）
            per_proj = 1 if ctx.dcfg["same_project_serial"] else ctx.dcfg["max_concurrent_per_project"]
            if ctx.project_active(pid) >= per_proj:
                continue
            c = ro()
            try:
                fails = fail_count(c, t["id"])
            finally:
                c.close()
            if not claim(ctx, t, 1 + fails):
                pending.remove(t)
                continue
            fut = ctx.pool.submit(worker, ctx, t, 1 + fails, fails)
            ctx.inflight[fut] = pid
            pending.remove(t)
        if not pending:
            break
        ctx.reap()
        if not ctx.inflight:
            log(logging.WARNING, "-", "round_blocked", "候选未能推进（并发/项目上限），本轮停止")
            break
        done, _ = wait(list(ctx.inflight), timeout=ctx.dcfg["dispatch_timeout_s"] + 30,
                       return_when=FIRST_COMPLETED)
        if not done:
            log(logging.WARNING, "-", "round_timeout", "等待 worker 超时，停止本轮")
            break
    if ctx.inflight:
        wait(list(ctx.inflight), timeout=ctx.dcfg["dispatch_timeout_s"] + 30)
        ctx.reap()


def install_signals(ctx):
    def handler(signum, frame):
        ctx.stop.set()
        log(logging.INFO, "-", "signal", f"收到信号 {signum}：不再领新单，等待当前 worker")
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), handler)
            except (ValueError, OSError):
                pass


def acquire_lock(ctx) -> int:
    """§6.2 单实例锁；存活 → 退出码 2（--force 可强制接管）；残留 → 接管并 warn。"""
    lp, pp = ctx.lock_path, ctx.pid_path

    def read_old_pid() -> int:
        try:
            return int(json.loads(lp.read_text(encoding="utf-8")).get("pid") or 0)
        except Exception:                                # noqa: BLE001
            return 0

    def deny(old: int) -> int:
        # C9：明确告知处理路径，不再只打印一句"已有派发器在跑"
        print(f"已有派发器在跑（pid={old}）。处理方式："
              f"python 派发器.py --stop 停止它；或 --force 强制接管（不推荐）。")
        return 2

    if lp.exists():
        old = read_old_pid()
        if pid_alive(old):
            if getattr(ctx.args, "force", False):
                log(logging.WARNING, "-", "lock_force",
                    f"--force 强制接管：旧进程 pid={old} 仍在运行，双实例风险自担")
            else:
                return deny(old)
        else:
            log(logging.WARNING, "-", "lock_stale", f"发现残留锁（pid={old} 已不存活），接管")
    try:
        fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started_at": 服务.now_iso()}, f, ensure_ascii=False)
    except FileExistsError:
        old = read_old_pid()
        if pid_alive(old) and not getattr(ctx.args, "force", False):
            return deny(old)
        lp.write_text(json.dumps({"pid": os.getpid(), "started_at": 服务.now_iso()},
                                 ensure_ascii=False), encoding="utf-8")
    pp.write_text(str(os.getpid()), encoding="utf-8")   # 急停端点读它
    ctx.holds_lock = True
    return 0


def _pid_is_python(pid: int) -> bool:
    """P3-2（复测）：taskkill 前确认目标进程确实是 Python，防 pid 被复用时误杀无关进程。"""
    if os.name != "nt":
        return True
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=8, **服务._child_kwargs())
        lines = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
        if not lines:
            return False
        img = lines[0].split('","')[0].strip('"')
        return "python" in img.lower()
    except Exception:                                    # noqa: BLE001
        return False


def stop_running(data_dir) -> int:
    """C9：--stop 停止运行中的派发器并清理锁/pid 文件；无实例时安静返回。"""
    pp = data_dir / 服务.DISPATCHER_PID.name
    pid = 0
    if pp.exists():
        try:
            pid = int(pp.read_text(encoding="utf-8").strip())
        except Exception:                                # noqa: BLE001
            pid = 0
    if pid and pid_alive(pid) and not _pid_is_python(pid):
        # P3-2（复测）：pid 复用嫌疑，宁可不动手
        print(f"拒绝停止：pid={pid} 不是 Python 进程（疑似 pid 已被复用），请人工确认后处理")
        return 3
    if pid and pid_alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, **服务._child_kwargs())
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        print(f"已停止派发器（pid={pid}）")
    elif pid:
        print(f"pid={pid} 已不存活，清理残留文件")
    else:
        print("未发现运行中的派发器（无 pid 文件）")
    for name in (服务.DISPATCHER_PID.name, 服务.DISPATCHER_LOCK.name):
        try:
            (data_dir / name).unlink(missing_ok=True)
        except Exception:                                # noqa: BLE001
            pass
    return 0


def cleanup_lock(ctx):
    if not ctx.holds_lock:
        return
    for p in (ctx.pid_path, ctx.lock_path):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    ctx.holds_lock = False


# ---------------- CLI ----------------

def parse_args(argv):
    ap = argparse.ArgumentParser(description="AI聊不停 · 真实自动派发器 M1")
    ap.add_argument("--once", action="store_true", help="跑一轮（默认）")
    ap.add_argument("--loop", action="store_true", help="按 interval_s 循环")
    ap.add_argument("--dry-run", action="store_true", help="只打印候选及取舍原因，不写任何表")
    ap.add_argument("--project", help="只处理指定项目")
    ap.add_argument("--ticket", help="只处理指定单号")
    ap.add_argument("--db", help="台账路径（不传即真实台账）")
    ap.add_argument("--config", help="配置路径")
    ap.add_argument("--data-dir", help="日志/锁目录（自检用，默认 服务.DATA）")
    ap.add_argument("--allow-real-db", action="store_true", help="显式允许操作真实台账")
    ap.add_argument("--init-db", action="store_true", help="仅临时库：调用 服务.init_db()")
    ap.add_argument("--stop", action="store_true", help="C9：停止运行中的派发器并清理锁文件")
    ap.add_argument("--force", action="store_true", help="C9：锁被存活进程持有时仍强制接管（不推荐）")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    # §2.2 路径覆盖：必须在任何 load_config()/db()/notify_boss() 之前
    if args.config:
        服务.CONFIG_PATH = Path(args.config).resolve()
    if args.db:
        服务.DB_PATH = Path(args.db).resolve()
    data_dir = Path(args.data_dir).resolve() if args.data_dir else Path(服务.DATA).resolve()
    setup_logging(data_dir)

    # C9：--stop 只停进程清锁文件，不碰台账，无需 --allow-real-db
    if args.stop:
        return stop_running(data_dir)

    if args.init_db and not args.db:
        print("--init-db 必须同时指定 --db（仅临时库允许建表/种演示数据）")
        return 4
    # §2.5 真实库保护阀
    if not args.db and not args.allow_real_db and not args.dry_run:
        print("检测到真实台账，需 --allow-real-db 才运行；建议先 --dry-run")
        return 4
    if args.init_db:
        服务.init_db()
    if not probe_tickets_table():
        print("台账缺少 tickets 表，拒绝在真实库隐式建表；请检查 --db 或用 --init-db --db <临时库>")
        return 3

    cfg = 服务.load_config()
    dcfg = dispatcher_cfg(cfg)
    if not dcfg["enabled"]:
        print("派发器已关闭")
        return 0

    lock_path = data_dir / 服务.DISPATCHER_LOCK.name
    pid_path = data_dir / 服务.DISPATCHER_PID.name
    ctx = Ctx(args, cfg, dcfg, data_dir, lock_path, pid_path)
    if not args.dry_run:
        rc = acquire_lock(ctx)
        if rc:
            return rc
    install_signals(ctx)
    try:
        while not ctx.stop.is_set():
            run_round(ctx)
            if args.dry_run or not args.loop:
                break
            end = time.time() + max(1, dcfg["interval_s"])
            while not ctx.stop.is_set() and time.time() < end:
                time.sleep(min(0.2, max(0.0, end - time.time())))
    finally:
        try:
            ctx.pool.shutdown(wait=True)
        except Exception:
            pass
        cleanup_lock(ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
