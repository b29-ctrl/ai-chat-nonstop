#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI聊不停 · 本地服务（零第三方依赖，仅标准库）

职责：
  1. 提供本地 HTTP API（127.0.0.1）
  2. SQLite 台账读写（tickets / events / signoffs / chat_messages / jobs / job_events / meta）
  3. 聊天适配器（WorkBuddy CLI / COZE API / Demo；DSH、CODEX 预留）
  4. 演示数据与演示状态推进（真实派发器接入后写同一张表即可）

启动：python 服务.py --port 8765
"""
from __future__ import annotations

import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / os.environ.get("AI_CHAT_WEB_DIR", "web")
DATA = ROOT / "数据"
DB_PATH = DATA / "台账.sqlite3"
CONFIG_PATH = ROOT / "配置.json"
ROLE_CATALOG_PATH = ROOT / "岗位目录.json"
ATTACH_DIR = DATA / "附件"
MAX_ATTACH = 20 * 1024 * 1024
DISPATCHER_PID = DATA / "派发器.pid"
DISPATCHER_LOCK = DATA / "派发器.lock"
LOCK = threading.RLock()

# 外部编排接口 v1（可选、幂等、只读为主）：不带 external_key / 不调新端点时行为零变化
EXTERNAL_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
EXTERNAL_KEY_MAX = 200
# notify_boss 去重：键 (project_id, ticket_id, gate, reason) → 上次发送时间
_NOTIFY_DEDUP: dict[tuple, float] = {}
_NOTIFY_LOCK = threading.Lock()
_NOTIFY_WINDOW_S = 60.0

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import 引导  # noqa: E402  首次运行自检（只读检测，不落盘）
import atexit  # noqa: E402
try:
    import 记忆  # noqa: E402  AI聊不停 长期记忆接入（默认关闭；模块缺失不影响任何现有行为）
except Exception:  # noqa: BLE001
    记忆 = None

APP_TITLE = "AI聊不停"
APP_VERSION = "0.6.0"

STATUSES = ["待处理", "派发中", "已回件", "待验", "已验", "失败"]

WORKBUDDY_MODELS = [
    {"id": "auto", "name": "自动（默认）", "desc": "由 WorkBuddy 服务端选择"},
    {"id": "hy4free", "name": "Hy4 Free（免费）", "desc": "混元 4 免费档"},
    {"id": "hy4std", "name": "Hy4 Standard", "desc": "混元 4 标准档"},
    {"id": "hy3", "name": "Hy3", "desc": "混元 3 思考模型"},
    {"id": "hy3-x", "name": "Hy3-X", "desc": "混元 3 增强"},
    {"id": "deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash", "desc": "1M 上下文，原生多模态"},
    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "desc": "DeepSeek 旗舰，1M 上下文"},
    {"id": "glm-5.3", "name": "GLM-5.3", "desc": "旗舰，擅长复杂编码与长程 Agent"},
    {"id": "glm-5.3-flash", "name": "GLM-5.3 Flash", "desc": "原生多模态，视觉推理"},
    {"id": "glm-5.3-flashx", "name": "GLM-5.3 FlashX", "desc": "原生多模态，视觉推理"},
    {"id": "glm-5.2", "name": "GLM-5.2", "desc": "通用"},
    {"id": "space-bunny", "name": "Space Bunny", "desc": "WorkBuddy 特供"},
    {"id": "minimax-m3.1-flash", "name": "MiniMax M3.1 Flash", "desc": "通用"},
    {"id": "minimax-m3-pay", "name": "MiniMax M3", "desc": "原生多模态"},
    {"id": "kimi-k3.1-pro-highspeed", "name": "Kimi K3.1 Pro 高速", "desc": "长上下文"},
    {"id": "kimi-k3.1-pro", "name": "Kimi K3.1 Pro", "desc": "长上下文"},
    {"id": "kimi-k3-2", "name": "Kimi K3", "desc": "长上下文、知识密集"},
    {"id": "kimi-k2.8-preview", "name": "Kimi K2.8 Preview", "desc": "长上下文预览版"},
    {"id": "step-5-preview", "name": "Step 5 Preview", "desc": "阶跃星辰预览版"},
    {"id": "mimo-v2.6-pro", "name": "MiMo V2.6 Pro", "desc": "通用"},
]
MODEL_LABELS = {m["id"]: m for m in WORKBUDDY_MODELS}
NEXT_STATE = {
    "待处理": "派发中",
    "派发中": "已回件",
    "已回件": "待验",
    "待验": "已验",
    "失败": "待处理",
}

DEFAULT_CONFIG = {
    "app": {"title": APP_TITLE, "version": APP_VERSION},
    "paths": {
        "project": "",
        "workbuddy_key": "",
        "coze_token": "",
        "workbuddy_cli": "",
        "node": "node",
        "dsh_cli": "",
        "codex_cli": "",
        "codex_home": "",
    },
    "adapters": {
        "workbuddy": {"type": "workbuddy", "enabled": False, "label": "WorkBuddy", "timeout_s": 180, "max_turns": 6,
                      "default_model": "hy4free", "models": WORKBUDDY_MODELS},
        "coze": {
            "type": "coze",
            "enabled": False,
            "label": "COZE",
            "bot_id": "",
            "user_id": "dsh-scheduler",
            "timeout_s": 120,
        },
        "dsh": {"type": "process", "enabled": False, "label": "DSH", "timeout_s": 300},
        "codex": {"type": "process", "enabled": False, "label": "CODEX", "timeout_s": 300},
    },
}


def _child_kwargs() -> dict:
    """Windows 下隐藏子进程控制台窗口（node/cmd 等）。"""
    if os.name != "nt":
        return {}
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}
    except Exception:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------- 搜索 / 时间轴 共用辅助（全程只读，不写库） ----------

def _int_param(q, name, default, maxv=None, fallback=None):
    """从 parse_qs 结果里安全取整数参数。非数字/越界按 fallback 或 default 回落，绝不抛 500。"""
    try:
        v = int((q.get(name) or [str(default)])[0])
    except (ValueError, TypeError):
        return fallback if fallback is not None else default
    if v <= 0:
        v = default
    if maxv is not None:
        v = min(v, maxv)
    return v


def _kw_like(kw):
    """把用户输入转成 LIKE 模式，并转义 % 和 _（避免「搜 % 返回全部」这类翻车）。"""
    e = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + e + "%"


def _bucket(role):
    """统一角色分桶：user / system / 其余（AI 发言、alert 等）算 assistant。"""
    r = (role or "").lower()
    if r == "user":
        return "user"
    if r == "system":
        return "system"
    return "assistant"


def _day_of(iso):
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", iso or "")
    return m.group(1) if m else ""


def _next_day(d):
    y, m, day = (int(x) for x in d.split("-"))
    return (datetime(y, m, day) + timedelta(days=1)).strftime("%Y-%m-%d")


def _norm_time(s):
    s = (s or "").strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return ("date", s)
    return ("iso", s)


def _sql_time_bounds(since_raw, until_raw):
    parts, params = [], []
    sn = _norm_time(since_raw)
    if sn:
        parts.append("created_at >= ?")
        params.append(sn[1])
    un = _norm_time(until_raw)
    if un:
        if un[0] == "date":
            parts.append("created_at < ?")
            params.append(_next_day(un[1]))
        else:
            parts.append("created_at <= ?")
            params.append(un[1])
    return parts, params


def _in_range(at, since_raw, until_raw):
    """纯 Python 侧时间过滤（主要用于运行时拼出来的系统事件 at）。"""
    sn = _norm_time(since_raw)
    if sn:
        if (at or "") < sn[1]:
            return False
    un = _norm_time(until_raw)
    if un:
        if un[0] == "date":
            if (at or "") >= _next_day(un[1]):
                return False
        else:
            if (at or "") > un[1]:
                return False
    return True


def _collect_search(conn, scope, target, project_id, q_text, role, since_raw, until_raw):
    """按关键词 + 时间 + 角色收集命中消息（只读）。
    返回列表，每条含原始字段 + type + at + 内部 _bucket。
    注意：项目群的系统事件是运行时拼出来的（project_feed），库里没有那句文本，
    所以必须取出来在 Python 侧做关键词过滤，再和群聊消息合并、统一排序。
    另外 project_feed 读 events JOIN tickets，是历史记录；但 JOIN 意味着
    工单被删或移出项目后，对应历史事件会消失——这是既有行为，本次不修。"""
    items = []
    kw = (q_text or "").strip()
    like = _kw_like(kw) if kw else None
    tb_parts, tb_params = _sql_time_bounds(since_raw, until_raw)

    if scope == "chat":
        sql = "SELECT * FROM chat_messages WHERE conversation_id=?"
        params = [target]
        if tb_parts:
            sql += " AND " + " AND ".join(tb_parts)
            params += tb_params
        if like:
            sql += " AND content LIKE ? ESCAPE '\\'"
            params.append(like)
        sql += " ORDER BY created_at ASC"
        for r in conn.execute(sql, params).fetchall():
            d = dict(r)
            d["type"] = "chat"
            d["at"] = d["created_at"]
            d["_bucket"] = _bucket(d.get("role"))
            items.append(d)
    else:  # project
        sql = "SELECT * FROM group_messages WHERE project_id=?"
        params = [project_id]
        if tb_parts:
            sql += " AND " + " AND ".join(tb_parts)
            params += tb_params
        if like:
            sql += " AND content LIKE ? ESCAPE '\\'"
            params.append(like)
        sql += " ORDER BY created_at ASC"
        for r in conn.execute(sql, params).fetchall():
            d = dict(r)
            try:
                d["mentions"] = json.loads(d.get("mentions") or "[]")
            except Exception:
                d["mentions"] = []
            d["type"] = "chat"
            d["at"] = d["created_at"]
            d["_bucket"] = _bucket(d.get("role"))
            items.append(d)
        # 系统事件：运行时拼接，必须在这里用关键词过滤（库里没有 text）
        for it in project_feed(conn, project_id, 100000):
            it = dict(it)
            if not _in_range(it.get("at"), since_raw, until_raw):
                continue
            if like and kw.lower() not in (it.get("text") or "").lower():
                continue
            it["type"] = "system"
            it["_bucket"] = "system"
            items.append(it)

    if role:
        items = [x for x in items if x.get("_bucket") == role]
    return items


def safe_filename(name: str) -> str:
    name = Path(str(name or "file")).name
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip().strip(".")
    if not name or name in (".", ".."):
        name = "file"
    return name[:120]


def save_attachment(name: str, mime: str, data: bytes) -> dict:
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    aid = uuid.uuid4().hex[:16]
    fn = safe_filename(name)
    path = ATTACH_DIR / f"{aid}_{fn}"
    path.write_bytes(data)
    conn = db()
    with conn:
        conn.execute(
            "INSERT INTO attachments(id,name,mime,size,path,created_at) VALUES(?,?,?,?,?,?)",
            (aid, fn, mime or "application/octet-stream", len(data), str(path), now_iso()),
        )
    conn.close()
    return {"id": aid, "name": fn, "mime": mime or "application/octet-stream", "size": len(data),
            "url": f"/api/attachments/{aid}"}


def attachment_meta(ids: list) -> list:
    if not ids:
        return []
    conn = db()
    out = []
    for aid in ids:
        r = conn.execute("SELECT id,name,mime,size FROM attachments WHERE id=?", (str(aid),)).fetchone()
        if r:
            d = dict(r)
            d["url"] = f"/api/attachments/{d['id']}"
            out.append(d)
    conn.close()
    return out


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            cfg = {}
    else:
        cfg = {}
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in cfg.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    # adapters 保持配置文件里的顺序（默认项补在后面），界面入口顺序=配置顺序
    if isinstance(cfg.get("adapters"), dict):
        merged["adapters"] = {
            **cfg["adapters"],
            **{k: v for k, v in merged["adapters"].items() if k not in cfg["adapters"]},
        }
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def ad_cfg(ad: dict, defaults: dict | None = None) -> dict:
    """执行端配置合并 defaults：defaults 里为 None 或缺失的键不覆盖 adapter。

    - 只做一层浅合并，不递归；
    - adapter 里显式写了的键永远优先；
    - defaults 为 None/空 时原样返回 ad 的副本（调用点旧行为不变）。
    """
    if not defaults:
        return dict(ad or {})
    base = {k: v for k, v in defaults.items() if v is not None}
    return {**base, **dict(ad or {})}


def ad_timeout(ad: dict, defaults: dict | None, kind: str, fallback: int) -> int:
    """执行端超时取值：ad.timeout_s > defaults[exec_timeout_s|http_timeout_s] > fallback。

    kind: "exec" = 进程/任务级（跑整个活）；"http" = 单次 HTTP 请求读超时。
    """
    if kind not in ("exec", "http"):
        raise ValueError(f"ad_timeout: 不支持的 kind={kind!r}（只接受 exec / http）")
    v = (ad or {}).get("timeout_s")
    if v in (None, "", 0):
        key = "exec_timeout_s" if kind == "exec" else "http_timeout_s"
        v = (defaults or {}).get(key)
    if v in (None, "", 0):
        return fallback
    try:
        return int(v)
    except (TypeError, ValueError):
        return fallback


CONFIG_LOCK = threading.Lock()


def save_config(cfg: dict):
    """原子写配置；只由本机 UI 的适配器改名等低频操作调用。"""
    with CONFIG_LOCK:
        tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)


def db():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    conn = db()
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                executor TEXT,
                model TEXT,
                recipient TEXT,
                gate TEXT,
                priority TEXT,
                resource TEXT,
                next_action TEXT,
                note TEXT,
                canonical_sha256 TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                at TEXT NOT NULL,
                kind TEXT NOT NULL,
                actor TEXT,
                detail TEXT
            );
            CREATE TABLE IF NOT EXISTS signoffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                gate TEXT,
                signer TEXT,
                at TEXT NOT NULL,
                note TEXT
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                adapter TEXT,
                model TEXT,
                attachments TEXT,
                ticket_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                target TEXT,
                model TEXT,
                status TEXT NOT NULL,
                error TEXT,
                ticket_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                at TEXT NOT NULL,
                type TEXT NOT NULL,
                content TEXT
            );
            CREATE TABLE IF NOT EXISTS meta (
                k TEXT PRIMARY KEY,
                v TEXT
            );
            CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                mime TEXT,
                size INTEGER,
                path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                dir TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                inject_profile INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_members (
                project_id TEXT NOT NULL,
                adapter_id TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY(project_id, adapter_id)
            );
            CREATE TABLE IF NOT EXISTS project_roles (
                project_id TEXT NOT NULL,
                role_id TEXT NOT NULL,
                adapter_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, role_id, adapter_id)
            );
            CREATE TABLE IF NOT EXISTS group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                adapter TEXT,
                mentions TEXT,
                attachments TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        # 轻量迁移：老库补 model / attachments / project_id 列
        for tbl in ("tickets", "chat_messages", "jobs"):
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            if "model" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN model TEXT")
            if "project_id" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN project_id TEXT")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
        if "attachments" not in cols:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN attachments TEXT")
        gcols = {row[1] for row in conn.execute("PRAGMA table_info(group_messages)").fetchall()}
        if "attachments" not in gcols:
            conn.execute("ALTER TABLE group_messages ADD COLUMN attachments TEXT")
        now = now_iso()
        pcols = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
        if "status" not in pcols:
            conn.execute("ALTER TABLE projects ADD COLUMN status TEXT NOT NULL DEFAULT 'running'")
        if "inject_profile" not in pcols:
            conn.execute("ALTER TABLE projects ADD COLUMN inject_profile INTEGER NOT NULL DEFAULT 1")
        conn.execute("INSERT OR IGNORE INTO projects(id,name,status,created_at,updated_at) VALUES('proj-default','默认项目','running',?,?)", (now, now))
        conn.execute("UPDATE tickets SET project_id='proj-default' WHERE project_id IS NULL OR project_id=''")
        conn.execute("UPDATE chat_messages SET project_id='proj-default' WHERE project_id IS NULL OR project_id=''")
        conn.execute("UPDATE jobs SET project_id='proj-default' WHERE project_id IS NULL OR project_id=''")
        # 外部编排接口 v1：幂等键列（旧库自动补，重复启动不报错）
        for tbl in ("tickets", "projects"):
            ocols = {row[1] for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            if "external_key" not in ocols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN external_key TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_tickets_external_key ON tickets(external_key) WHERE external_key IS NOT NULL")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_external_key ON projects(external_key) WHERE external_key IS NOT NULL")
        # 项目归档（CB-UI-PROJ-20261112）：旧库幂等补列，不重建表、不动既有数据
        acols = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
        if "archived" not in acols:
            conn.execute("ALTER TABLE projects ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        if "archived_at" not in acols:
            conn.execute("ALTER TABLE projects ADD COLUMN archived_at TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_archived ON projects(archived)")
        # 默认项目：把当前已启用的适配器都放进群，老用户升级后不至于空群
        try:
            cfg0 = load_config()
            for aid, ad in (cfg0.get("adapters") or {}).items():
                if is_enabled(ad):
                    conn.execute("INSERT OR IGNORE INTO project_members(project_id,adapter_id,added_at) VALUES('proj-default',?,?)", (aid, now))
        except Exception:
            pass
    if conn.execute("SELECT COUNT(*) AS c FROM tickets").fetchone()["c"] == 0:
        seed_demo(conn)
    conn.execute("UPDATE tickets SET project_id='proj-default' WHERE project_id IS NULL OR project_id=''")
    # 演示单：WorkBuddy 默认先用免费档（只动 demo/manual 台账）
    conn.execute("UPDATE tickets SET model='hy4free' WHERE executor='WORKBUDDY' AND (model IS NULL OR model='') AND canonical_sha256 IN ('demo','manual')")
    conn.commit()
    conn.close()


def add_event(conn, ticket_id: str, kind: str, actor: str, detail: str):
    conn.execute(
        "INSERT INTO events(ticket_id, at, kind, actor, detail) VALUES(?,?,?,?,?)",
        (ticket_id, now_iso(), kind, actor, detail),
    )


# ---------------- 外部编排接口 v1：可选辅助（缺省完全等价旧行为） ----------------

def _orch_cfg(cfg: dict) -> tuple[bool, int]:
    """读可选 orchestration 段；整段删除时返回默认 (True, 100)。"""
    o = (cfg or {}).get("orchestration") or {}
    try:
        limit = int(o.get("outbox_limit", 100))
    except (TypeError, ValueError):
        limit = 100
    return bool(o.get("enabled", True)), max(1, min(500, limit))


def _parse_external_key(body: dict) -> tuple[str | None, str | None]:
    """返回 (key, error)。字段缺失 / None → 视为不带（旧行为）；非法（含空串）→ 400 文案。"""
    if "external_key" not in body or body.get("external_key") is None:
        return None, None
    v = body.get("external_key")
    if not isinstance(v, str) or len(v) > EXTERNAL_KEY_MAX or not EXTERNAL_KEY_RE.fullmatch(v):
        return None, f"external_key 非法：需为 ≤{EXTERNAL_KEY_MAX} 的 [A-Za-z0-9._:-] 字符串"
    return v, None


_ORCH_LOG_PATH = Path(os.environ.get("ORCH_LOG_PATH") or (DATA / "编排接入.log"))
_ORCH_LOG_LOCK = threading.Lock()


def _orch_log(msg: str) -> None:
    """编排降级/告警日志（只读记录，失败静默）；路径可由 ORCH_LOG_PATH 重定向。"""
    try:
        with _ORCH_LOG_LOCK:
            _ORCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_ORCH_LOG_PATH, "a", encoding="utf-8", newline="\n") as f:
                f.write(f"{now_iso()} {msg}\n")
    except Exception:
        pass


def _parse_limit(raw, default: int, cap: int, floor: int = 0) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = default
    return max(floor, min(cap, n))


def new_ticket_id(recipient: str) -> str:
    code = {"豆包": "DB", "CODEX": "CX", "DSH": "DSH", "用户": "U"}.get(recipient, "U")
    return f"WO-{code}-{code}-{uuid.uuid4().hex[:8]}"


def seed_demo(conn):
    t = now_iso()
    rows = [
        # id, title, status, executor, recipient, gate, priority, next_action, note
        ("WO-DB-WB-8a1f2c3d", "阶段3 构建与真机冒烟", "派发中", "WORKBUDDY", "豆包", "门③", "P1",
         "等待 WorkBuddy 返回构建与冒烟证据", "替代豆包的 AI-B 构建/排查席位"),
        ("WO-DB-CX-3f2c9d1e", "导出模块实现（第 3 轮）", "已回件", "CODEX", "CODEX", "门②", "P1",
         "等待主理人/审计 AI 复核", "代码子树干净，产物已回件"),
        ("WO-DB-DSH-5b7e9a2c", "工单自动流转方案 v1.8 评审", "待验", "DSH", "DSH", "门②", "P1",
         "门② 待评审", "方案已出，等审计意见"),
        ("WO-DB-WB-2d4e6f8a", "真机回归 + 日志排查", "待验", "WORKBUDDY", "豆包", "门④", "P1",
         "门④ 待独立验收", "只读排查单，不修 bug"),
        ("WO-DB-CX-9c1b3d5e", "修复导出空数组 bug", "待处理", "CODEX", "CODEX", "门②", "P0",
         "等待派发", "来自 WorkBuddy 排查单"),
        ("WO-DB-WB-7f2a4c6e", "阶段2 产物 BUG 排查（只读）", "派发中", "WORKBUDDY", "豆包", "无", "P1",
         "等待 WorkBuddy 输出 bug 清单", "排查单：Read/Grep + 测试命令"),
        ("WO-DB-COZE-1a3c5e7f", "回报格式一致性核对", "已验", "COZE", "豆包", "门②", "P2",
         "已过门②", "云端评审，调度器落盘"),
        ("WO-DB-CX-6e8a0c2b", "旧版构建失败（示例）", "失败", "CODEX", "CODEX", "门③", "P1",
         "看板告警，等待重派", "exit≠0，已记录失败原因"),
    ]
    for idx, (rid, title, status, executor, recipient, gate, prio, nxt, note) in enumerate(rows):
        created = t
        conn.execute(
            "INSERT INTO tickets(id,title,status,executor,recipient,gate,priority,resource,next_action,note,canonical_sha256,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, title, status, executor, recipient, gate, prio, "repo:demo", nxt, note,
             "demo", created, t),
        )
        add_event(conn, rid, "intake_seen", "派发器", "首次扫描到工单")
        add_event(conn, rid, "dispatch_intent", "派发器", f"尝试 1：{executor}")
        if status != "待处理":
            add_event(conn, rid, "dispatch_ok", "派发器", f"{executor} 已启动/已投递")
        if status in ("已回件", "待验", "已验"):
            add_event(conn, rid, "reply_seen", "派发器", "收到回报，单号/状态头校验通过")
        if status == "已验":
            add_event(conn, rid, "gate_passed", "主理人", f"{gate} 已签")
            conn.execute(
                "INSERT INTO signoffs(ticket_id, gate, signer, at, note) VALUES(?,?,?,?,?)",
                (rid, gate, "主理人", t, "演示签收"),
            )
        if status == "失败":
            add_event(conn, rid, "dispatch_fail", "派发器", "exit=1：构建失败，等待重派")
    conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('last_tick',?)", (t,))
    conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('demo', '1')")
    conn.commit()


# ---------------- 适配器 ----------------

def read_secret(path: str) -> str:
    p = Path(resolve_path(path))
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    # 允许 CODEBUDDY_API_KEY=xxx 或单独一行；剥掉 < >
    m = re.search(r"CODEBUDDY_API_KEY\s*=\s*([^\s+]+)", text)
    val = m.group(1) if m else text.strip().splitlines()[0] if text.strip() else ""
    val = val.strip().strip("<>").strip()
    return val


def workbuddy_key_path(cfg: dict) -> str:
    """返回实际使用的 WorkBuddy Key 文件；优先显式配置，再找用户目录兜底。"""
    p = Path(resolve_path((cfg.get("paths") or {}).get("workbuddy_key", "")))
    if p.exists():
        return str(p)
    fallback = Path.home() / ".ai-chat-nonstop" / "workbuddy_key.txt"
    if fallback.exists():
        return str(fallback)
    return str(p)


def workbuddy_key(cfg: dict) -> str:
    return (os.environ.get("CODEBUDDY_API_KEY", "").strip()
            or read_secret(workbuddy_key_path(cfg)))


def adapter_key_path(cfg: dict, ad: dict | None, kind: str) -> tuple[str, str]:
    """R1-A：adapter 级 Key/Token 引用优先，否则回落全局。
    返回 (实际路径, 来源标记)；旧配置没有 key_ref/token_ref 时行为完全不变。"""
    a = ad or {}
    paths = cfg.get("paths") or {}
    ref_key = "key_ref" if kind == "workbuddy" else "token_ref"
    ref = str(a.get(ref_key) or "").strip()
    if ref and ref in paths:
        return resolve_path(paths.get(ref) or ""), f"adapter:{ref}"
    if kind == "workbuddy":
        return workbuddy_key_path(cfg), "global:workbuddy_key"
    return resolve_path(paths.get("coze_token", "")), "global:coze_token"


MODELS_CACHE = DATA / "models.json"


def get_workbuddy_models(cfg: dict | None = None) -> list:
    """优先用实测缓存；否则用配置；最后用内置列表。"""
    if MODELS_CACHE.exists():
        try:
            cached = json.loads(MODELS_CACHE.read_text(encoding="utf-8"))
            ids = [x for x in cached.get("ids", []) if isinstance(x, str)]
            if ids:
                out = []
                for mid in ids:
                    meta = MODEL_LABELS.get(mid, {})
                    out.append({"id": mid, "name": meta.get("name", mid), "desc": meta.get("desc", "实测可用")})
                return out
        except Exception:
            pass
    if cfg is None:
        cfg = load_config()
    ms = cfg["adapters"].get("workbuddy", {}).get("models")
    return ms if isinstance(ms, list) and ms else WORKBUDDY_MODELS


def refresh_workbuddy_models() -> tuple[bool, str, list]:
    """用 CLI 的非法模型探测账号可用模型列表（不消耗 Token），并缓存。"""
    cfg = load_config()
    paths = cfg["paths"]
    key = workbuddy_key(cfg)
    if not key:
        return False, f"找不到 WorkBuddy API Key：{workbuddy_key_path(cfg)}", []
    cli = resolve_path(paths["workbuddy_cli"])
    if not Path(cli).exists():
        return False, "找不到 WorkBuddy CLI", []
    env = os.environ.copy()
    env["CODEBUDDY_FORCE_LITE_WB_BUNDLE"] = "1"
    env["CODEBUDDY_API_KEY"] = key
    env.pop("CODEBUDDY_API_KEY_DISABLED", None)
    node = paths.get("node") or "node"
    cmd = [node, cli, "-p", "--output-format", "json", "--max-turns", "1",
           "--model", "__dsh_probe_invalid__", "--", "x"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=45, env=env,
                              cwd=resolve_path(paths.get("project") or str(ROOT)),
                              text=True, encoding="utf-8", errors="replace",
                              **_child_kwargs())
    except Exception as e:  # noqa: BLE001
        return False, f"探测失败：{e}", []
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    m = re.search(r"Currently supported models for your account:(.*?)(?:\nPlease use|\Z)", text, re.S)
    ids = re.findall(r"^\s*-\s*([A-Za-z0-9._-]+)\s*$", m.group(1) if m else "", re.M)
    ids = [x for x in ids if x and not x.startswith("__dsh")]
    if not ids:
        return False, "没有解析到模型列表（CLI 输出格式可能变了）", []
    # auto 放最前，其余保持 CLI 顺序
    ids = ["auto"] + [x for x in ids if x != "auto"]
    DATA.mkdir(parents=True, exist_ok=True)
    MODELS_CACHE.write_text(json.dumps({"at": now_iso(), "ids": ids}, ensure_ascii=False, indent=2), encoding="utf-8")
    out = []
    for mid in ids:
        meta = MODEL_LABELS.get(mid, {})
        out.append({"id": mid, "name": meta.get("name", mid), "desc": meta.get("desc", "实测可用")})
    return True, f"已刷新，共 {len(out)} 个模型", out


def workbuddy_chat(cfg: dict, prompt: str, model: str | None = None, ad: dict | None = None) -> str:
    paths = cfg["paths"]
    a = ad or cfg["adapters"].get("workbuddy") or next(
        (v for v in cfg["adapters"].values() if (v.get("type") or "").lower() == "workbuddy"), {})
    defaults = cfg.get("defaults")
    key_path, ksrc = adapter_key_path(cfg, a, "workbuddy")
    # R1-A：有 adapter 级 key_ref 就用它；否则沿用旧的全局/env 逻辑
    key = read_secret(key_path) if ksrc.startswith("adapter:") else workbuddy_key(cfg)
    if not key:
        return (f"【WorkBuddy 未配置】找不到 API Key：{key_path} 不存在"
                f"（{ksrc}）。请把平台 Key 放到该文件，或设置 CODEBUDDY_API_KEY 环境变量。")
    cli = resolve_path(paths["workbuddy_cli"])
    if not Path(cli).exists():
        return f"【WorkBuddy 未找到 CLI】{cli}"
    model = (model or a.get("default_model") or "auto").strip()
    env = os.environ.copy()
    env["CODEBUDDY_FORCE_LITE_WB_BUNDLE"] = "1"
    env["CODEBUDDY_API_KEY"] = key
    env.pop("CODEBUDDY_API_KEY_DISABLED", None)
    node = paths.get("node") or "node"
    # R9.1：放行只读/编辑工具（与 codebuddy-cn.command 已生效白名单一致）；
    # ad.allowed_tools 为列表时以其为准（便于按岗收紧），否则用默认白名单。不改 max-turns/Key 逻辑。
    allowed = [str(x) for x in a["allowed_tools"]] if isinstance(a.get("allowed_tools"), list) else [
        "Read", "Glob", "Grep", "Edit", "Write", "Bash"]
    cmd = [node, cli, "-p", "--output-format", "json", "--max-turns", str(a.get("max_turns", 6)),
           "--permission-mode", "acceptEdits", "--allowedTools", *allowed]
    if model:
        cmd += ["--model", model]
    cmd += ["--", prompt]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=ad_timeout(a, defaults, "exec", 180),
            env=env, cwd=resolve_path(paths.get("project") or str(ROOT)),
            text=True, encoding="utf-8", errors="replace",
            **_child_kwargs(),
        )
    except subprocess.TimeoutExpired:
        _t = ad_timeout(a, defaults, "exec", 180)
        return f"【WorkBuddy 超时】{_t} 秒未返回，已终止。"
    except Exception as e:  # noqa: BLE001
        return f"【WorkBuddy 启动失败】{e}"
    out = (proc.stdout or "").strip()
    if not out:
        return f"【WorkBuddy 无输出】exit={proc.returncode}\n{(proc.stderr or '')[-800:]}"
    result = None
    try:
        start = out.find("[")
        arr = json.loads(out[start:] if start >= 0 else out)
        last = arr[-1] if isinstance(arr, list) and arr else arr
        if isinstance(last, dict):
            if last.get("is_error"):
                return f"【WorkBuddy 返回错误】{last.get('result') or last}"
            result = str(last.get("result") or last)
    except Exception:
        pass
    if result is None:
        result = out[-4000:]
    # C1：退出码非 0 → 标记失败（同 process_chat 的 _exit_code_guard 口径）
    return _exit_code_guard("WorkBuddy", proc.returncode, (proc.stderr or ""), result)


def coze_chat(cfg: dict, prompt: str, ad: dict | None = None) -> str:
    paths = cfg["paths"]
    a = ad or cfg["adapters"].get("coze") or next(
        (v for v in cfg["adapters"].values() if (v.get("type") or "").lower() == "coze"), {})
    defaults = cfg.get("defaults")
    token_path, tsrc = adapter_key_path(cfg, a, "coze")
    # R1-A：有 adapter 级 token_ref 就用它；否则沿用全局 paths.coze_token
    token = read_secret(token_path)
    if not token:
        return f"【COZE 未配置】找不到 API Token：{token_path} 不存在（{tsrc}）。"
    base = "https://api.coze.cn"

    def call(url: str, body: dict | None, method: str = "POST") -> dict:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data if method == "POST" else None, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    try:
        r = call(f"{base}/v3/chat", {
            "bot_id": a["bot_id"], "user_id": a.get("user_id", "dsh-scheduler"),
            "stream": False, "auto_save_history": True,
            "additional_messages": [{"role": "user", "content": prompt, "content_type": "text"}],
        })
        if r.get("code") != 0:
            return f"【COZE 错误】code={r.get('code')} {r.get('msg')}"
        chat_id = r["data"]["id"]
        conv = r["data"]["conversation_id"]
        deadline = time.time() + ad_timeout(a, defaults, "exec", 120)
        status = "in_progress"
        while time.time() < deadline:
            q = urllib.parse.urlencode({"conversation_id": conv, "chat_id": chat_id})
            rr = call(f"{base}/v3/chat/retrieve?{q}", None, "GET")
            status = rr.get("data", {}).get("status", status)
            if status in ("completed", "failed", "requires_action", "canceled"):
                break
            time.sleep(1)
        if status != "completed":
            return f"【COZE 未完成】status={status}"
        q = urllib.parse.urlencode({"conversation_id": conv, "chat_id": chat_id})
        ml = call(f"{base}/v3/chat/message/list?{q}", None, "GET")
        parts = [m.get("content", "") for m in ml.get("data", []) if m.get("role") == "assistant" and m.get("type") == "answer"]
        return "\n".join(p for p in parts if p) or "【COZE 无答案】"
    except Exception as e:  # noqa: BLE001
        return f"【COZE 调用失败】{e}"


def demo_chat(cfg: dict, prompt: str) -> str:
    time.sleep(0.25)
    return (
        f"【演示回复 · {APP_TITLE}】\n"
        f"收到：{prompt[:120]}\n\n"
        "这是本地演示适配器，不需要任何 Key。\n"
        "接入真实适配器后，这里会显示 WorkBuddy / COZE / DSH / CODEX 的回复；\n"
        "调度器派单、工单台账、门禁签收都在右侧「工单」里。"
    )


def coze_bot_info() -> dict:
    cfg = load_config()
    ad = cfg["adapters"].get("coze") or next(
        (v for v in cfg["adapters"].values() if (v.get("type") or "").lower() == "coze"), None)
    if not ad:
        return {"ok": False, "error": "当前配置里没有 COZE 适配器"}
    token = read_secret(cfg["paths"]["coze_token"])
    if not token:
        return {"ok": False, "error": "找不到 COZE Token"}
    bot_id = ad.get("bot_id", "")
    try:
        req = urllib.request.Request(
            f"https://api.coze.cn/v1/bots/{bot_id}?is_published=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        if j.get("code") != 0:
            return {"ok": False, "error": f"code={j.get('code')} {j.get('msg')}"}
        d = j.get("data") or {}
        mi = d.get("model_info") or {}
        return {
            "ok": True,
            "bot_id": bot_id,
            "name": d.get("name"),
            "version": d.get("version"),
            "model_id": mi.get("model_id"),
            "model_params": mi.get("parameters"),
            "context_round": mi.get("context_round"),
            "knowledge_count": len((d.get("knowledge") or {}).get("knowledge_infos") or []),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def resolve_path(p: str) -> str:
    if not p:
        return ""
    return os.path.expandvars(os.path.expanduser(str(p)))


def find_adapter(cfg: dict, target: str):
    """按 ID 找适配器；兼容大小写（WORKBUDDY / workbuddy）。"""
    if not target:
        return None
    ad = cfg.get("adapters", {}).get(target)
    if ad is not None:
        return ad
    for k, v in cfg.get("adapters", {}).items():
        if k.lower() == str(target).lower():
            return v
    return None


def adapter_models(cfg: dict, ad: dict | None) -> list:
    if not ad:
        return []
    t = (ad.get("type") or "").lower()
    if t == "workbuddy":
        return get_workbuddy_models(cfg)
    ms = ad.get("models")
    return ms if isinstance(ms, list) else []


def adapter_models_for_target(cfg: dict, target: str) -> list:
    return adapter_models(cfg, find_adapter(cfg, target))


def _fmt(s, cfg: dict, prompt: str, model: str, cwd: str) -> str:
    s = str(s)
    for k, v in cfg.get("paths", {}).items():
        s = s.replace("{" + k + "}", resolve_path(v))
    s = s.replace("{prompt}", prompt).replace("{model}", model or "")
    s = s.replace("{cwd}", cwd).replace("{home}", str(Path.home()))
    return s


def _adapter_env(cfg: dict, ad: dict, prompt: str, model: str, cwd: str) -> dict:
    env = os.environ.copy()
    for k, v in (ad.get("env") or {}).items():
        env[str(k)] = _fmt(v, cfg, prompt, model, cwd)
    for env_key, ref in (ad.get("secret_env") or {}).items():
        if isinstance(ref, dict):
            file_ref = str(ref.get("file") or "")
            if not (file_ref.startswith("{") and file_ref.endswith("}")):
                continue
            pkey = file_ref[1:-1]
            if pkey not in (cfg.get("paths") or {}):
                continue
            path = resolve_path(cfg["paths"][pkey])
            val = read_yaml_value(path, ref.get("key") or "")
        else:
            if ref not in (cfg.get("paths") or {}):
                continue
            path = resolve_path(cfg["paths"][ref])
            val = read_secret(path) if Path(path).exists() else ""
        if val:
            env[str(env_key)] = val
    return env


def read_yaml_value(path: str, key: str) -> str:
    p = Path(resolve_path(path))
    if not p.exists() or not key:
        return ""
    leaf = str(key).split(".")[-1]
    try:
        for line in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            m = re.match(rf"^\s*{re.escape(leaf)}\s*:\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _dig(obj, path: str):
    if not path:
        return obj
    cur = obj
    for part in str(path).split("."):
        if part == "last":
            cur = cur[-1] if isinstance(cur, list) and cur else None
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _json_from_text(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                continue
    return None


def _agent_message_from_jsonl(text: str):
    """从 codex --json 输出（JSONL）中提取最后一条 agent_message 文本。"""
    msgs = []
    errs = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        typ = obj.get("type")
        if typ == "item.completed":
            item = obj.get("item") or {}
            it = item.get("type")
            if it == "agent_message" and isinstance(item.get("text"), str) and item["text"].strip():
                msgs.append(item["text"].strip())
            elif it == "error" and item.get("message"):
                errs.append(str(item["message"]).strip())
    if msgs:
        return msgs[-1]
    if errs:
        return "【codex 错误】" + errs[-1]
    return None


def sanitize(text) -> str:
    s = str(text or "")
    s = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "sk-***", s)
    s = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1***", s)
    s = re.sub(r"(?i)(CODEBUDDY_API_KEY\s*=\s*)\S+", r"\1***", s)
    s = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)\S+", r"\1***", s)
    return s


def is_enabled(ad: dict | None) -> bool:
    if not ad:
        return False
    v = ad.get("enabled", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(v)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _validate_outbound_url(url: str, allow_local: bool = False) -> str:
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("https", "http"):
        return "仅支持 http(s)"
    if u.scheme != "https" and not allow_local:
        return "非 https 被拒绝（需要 allow_local=true）"
    host = (u.hostname or "").lower()
    if not host:
        return "URL 缺少 host"
    if allow_local:
        return ""
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return "禁止环回地址（需要 allow_local=true）"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            return "禁止私网/环回地址（需要 allow_local=true）"
    except ValueError:
        pass
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def validate_process_adapter(cfg: dict, ad: dict, prompt: str, model: str, cwd: str) -> str:
    raw = ad.get("command")
    if not raw:
        return "process 适配器未配置 command"
    cmd = shlex.split(raw) if isinstance(raw, str) else [str(x) for x in raw]
    sec = cfg.get("security") or {}
    forbidden = tuple(sec.get("process_forbidden_flags") or
                      ["--dangerously-", "--bypass", "--no-sandbox", "--skip-permissions"])
    for tok in cmd:
        if str(tok).lower().startswith(forbidden):
            return f"process 命令含危险参数：{tok}"
    first = _fmt(cmd[0], cfg, prompt, model, cwd)
    allowed = {resolve_path(v) for v in (cfg.get("paths") or {}).values() if isinstance(v, str)}
    allowed |= set(sec.get("process_allowed_executables") or [])
    if first not in allowed:
        return f"process command[0] 不在白名单：{first}"
    return ""


def _exit_code_guard(label: str, returncode, errout: str, result: str) -> str:
    """C1：进程退出码非 0 → 即使有输出也打上失败标记（【…退出码异常…】命中派发器 FAIL_RE），
    避免真机 T04「崩溃但打印了东西」被误判成功；回件保留输出与 stderr 尾部便于诊断。"""
    if returncode in (0, None):
        return result
    tail = f"{errout[-400:]}\n----\n{result[:1200]}".strip()
    return sanitize(f"【{label} 退出码异常 exit={returncode}】\n{tail}")


def process_chat(cfg: dict, ad: dict, prompt: str, model: str | None = None) -> str:
    defaults = cfg.get("defaults")
    raw = ad.get("command")
    if not raw:
        return "【process 适配器未配置 command】"
    label = ad.get("label") or "CLI"
    if not model:
        models = adapter_models(cfg, ad)
        model = ad.get("default_model") or (models[0]["id"] if models else None)
    cmd = shlex.split(raw) if isinstance(raw, str) else [str(x) for x in raw]
    base_cwd = resolve_path(cfg.get("paths", {}).get("project") or str(ROOT))
    cwd = resolve_path(_fmt(ad.get("cwd") or base_cwd, cfg, prompt, model or "", base_cwd))
    project = resolve_path(cfg.get("paths", {}).get("project") or "")
    if project and not _is_relative_to(Path(cwd), Path(project)):
        return f"【{label}】cwd 不在项目目录内，拒绝执行"
    err = validate_process_adapter(cfg, ad, prompt, model or "", cwd)
    if err:
        return f"【{label}】{err}"
    argv = [_fmt(x, cfg, prompt, model or "", cwd) for x in cmd]
    stdin_text = prompt if ad.get("stdin") else None
    joined = " ".join(str(x) for x in cmd)
    if stdin_text is None and "{prompt}" not in joined:
        argv += ["--", prompt]
    env = _adapter_env(cfg, ad, prompt, model or "", cwd)
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE if stdin_text is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
            text=True, encoding=ad.get("encoding") or "utf-8", errors="replace",
            start_new_session=not sys.platform.startswith("win"),
            **_child_kwargs(),
        )
        try:
            out, errout = proc.communicate(stdin_text, timeout=ad_timeout(ad, defaults, "exec", 300))
        except subprocess.TimeoutExpired:
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, **_child_kwargs())
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
            proc.communicate()
            return f"【{label} 超时】"
    except Exception as e:  # noqa: BLE001
        return sanitize(f"【{label} 启动失败】{e}")
    out = sanitize((out or "").strip())
    errout = sanitize((errout or "").strip())
    if not out:
        return sanitize(f"【{label} 无输出】exit={proc.returncode}\n{errout[-800:]}")
    if (ad.get("output") or "text").lower() == "json":
        data = _json_from_text(out)
        if data is not None:
            # P2-2（复测）：json 分支所有出口统一收口到退出码守卫，
            # 堵死「崩溃但吐出可解析 JSON」被判成功（minimax/qoder 类 CLI）
            if ad.get("result_path"):
                val = _dig(data, ad["result_path"])
                if val is not None:
                    return _exit_code_guard(label, proc.returncode, errout, sanitize(str(val)))
            if isinstance(data, list) and data:
                last = data[-1]
                if isinstance(last, dict):
                    if last.get("is_error"):
                        return sanitize(f"【{label} 返回错误】{last.get('result') or last}")
                    if last.get("result") is not None:
                        return _exit_code_guard(label, proc.returncode, errout, sanitize(str(last["result"])))
                return _exit_code_guard(label, proc.returncode, errout, sanitize(str(last)))
            if isinstance(data, dict):
                for key in ("result", "reply", "content", "output", "text", "message"):
                    if data.get(key) is not None:
                        return _exit_code_guard(label, proc.returncode, errout, sanitize(str(data[key])))
                return _exit_code_guard(label, proc.returncode, errout,
                                        sanitize(json.dumps(data, ensure_ascii=False)))
    if (ad.get("output") or "text").lower() == "jsonl":
        msg = _agent_message_from_jsonl(out)
        if msg:
            return _exit_code_guard(label, proc.returncode, errout, sanitize(msg))
        return sanitize(f"【{label} 无回件】{out[-800:]}")
    return _exit_code_guard(label, proc.returncode, errout, out[-6000:])


def openai_chat(cfg: dict, ad: dict, prompt: str, model: str | None = None) -> str:
    defaults = cfg.get("defaults")
    base = str(ad.get("base_url") or "").rstrip("/")
    if not base:
        return "【OpenAI 兼容适配器未配置 base_url】"
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    err = _validate_outbound_url(url, bool(ad.get("allow_local")))
    if err:
        return f"【OpenAI 兼容 URL 拒绝】{err}"
    key = ""
    if ad.get("api_key_file"):
        path = resolve_path(cfg.get("paths", {}).get(ad["api_key_file"], ad["api_key_file"]))
        key = read_secret(path)
    if not key and ad.get("api_key_env"):
        key = os.environ.get(ad["api_key_env"], "")
    if not key:
        return "【OpenAI 兼容适配器未配置 API Key（api_key_env / api_key_file）】"
    mdl = model or ad.get("model") or "default"
    body = {
        "model": mdl,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": ad.get("temperature", 0.3),
        "stream": False,
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with _OPENER.open(req, timeout=ad_timeout(ad, defaults, "http", 180)) as r:
            j = json.loads(r.read().decode("utf-8"))
        return str(((j.get("choices") or [{}])[0].get("message") or {}).get("content") or json.dumps(j, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        return f"【OpenAI 兼容调用失败】{e}"


def a2a_chat(cfg: dict, ad: dict, prompt: str, model: str | None = None) -> str:
    defaults = cfg.get("defaults")
    url = ad.get("url") or ""
    if not url:
        return "【A2A 适配器未配置 url】"
    err = _validate_outbound_url(url, bool(ad.get("allow_local")))
    if err:
        return f"【A2A URL 拒绝】{err}"
    payload = {
        "jsonrpc": "2.0", "id": uuid.uuid4().hex[:8], "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": prompt}]}},
    }
    headers = {"Content-Type": "application/json"}
    key = ""
    if ad.get("api_key_env"):
        key = os.environ.get(ad["api_key_env"], "")
    if ad.get("api_key_file"):
        key = key or read_secret(resolve_path(cfg.get("paths", {}).get(ad["api_key_file"], ad["api_key_file"])))
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers)
    try:
        with _OPENER.open(req, timeout=ad_timeout(ad, defaults, "http", 300)) as r:
            text = r.read().decode("utf-8", errors="replace")
        data = _json_from_text(text)
        if data is not None:
            result = data.get("result", data)
            texts = []
            def walk(o):
                if isinstance(o, dict):
                    if o.get("kind") == "text" and isinstance(o.get("text"), str):
                        texts.append(o["text"])
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(result)
            if texts:
                return "\n".join(texts)
            if isinstance(result, dict) and result.get("parts"):
                return "\n".join(str(p.get("text", p)) for p in result["parts"])
            return str(result)
        return text[-4000:]
    except Exception as e:  # noqa: BLE001
        return f"【A2A 调用失败】{e}"


def webhook_chat(cfg: dict, ad: dict, prompt: str, model: str | None = None) -> str:
    defaults = cfg.get("defaults")
    url = ad.get("url") or ""
    if not url:
        return "【webhook 适配器未配置 url】"
    err = _validate_outbound_url(url, bool(ad.get("allow_local")))
    if err:
        return f"【webhook URL 拒绝】{err}"
    body = {"prompt": prompt, "model": model, "source": "ticket-deck"}
    headers = {"Content-Type": "application/json"}
    key = ""
    if ad.get("api_key_env"):
        key = os.environ.get(ad["api_key_env"], "")
    if ad.get("api_key_file"):
        key = key or read_secret(resolve_path(cfg.get("paths", {}).get(ad["api_key_file"], ad["api_key_file"])))
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers=headers)
    try:
        with _OPENER.open(req, timeout=ad_timeout(ad, defaults, "http", 180)) as r:
            text = r.read().decode("utf-8", errors="replace")
        data = _json_from_text(text)
        if isinstance(data, dict):
            for k in ("reply", "result", "content", "output", "text"):
                if data.get(k) is not None:
                    return str(data[k])
        return text[-4000:]
    except Exception as e:  # noqa: BLE001
        return f"【webhook 调用失败】{e}"


def pull_chat(cfg: dict, ad: dict, prompt: str, model: str | None = None) -> str:
    outbox = resolve_path(ad.get("outbox") or str(ROOT / "数据" / "发件箱"))
    if not ad.get("allow_outside_write"):
        base = Path(resolve_path(cfg.get("paths", {}).get("project") or str(ROOT)))
        if not _is_relative_to(Path(outbox), base):
            return "【pull 适配器】outbox 不在项目目录内，拒绝写入（需要 allow_outside_write=true）"
    name = str(ad.get("name_template") or "deck-{id}.md")
    if name != Path(name).name or ".." in name or "/" in name or "\\" in name:
        return "【pull 适配器】name_template 非法"
    Path(outbox).mkdir(parents=True, exist_ok=True)
    fn = Path(outbox) / name.replace("{id}", uuid.uuid4().hex[:10])
    fn.write_text(
        f"# AI聊不停 · 待拉取消息\n\n> 创建时间：{now_iso()}\n\n{sanitize(prompt)}\n",
        encoding="utf-8",
    )
    reply_dir = ad.get("reply_dir")
    return f"【pull 型适配器】已投递到 {fn.name}\n它不会实时回复；请对方拉取后在 {reply_dir or '约定回执目录'} 写回执。"


def dispatch_chat(cfg: dict, target: str, prompt: str, model: str | None = None) -> str:
    ad = find_adapter(cfg, target)
    if not ad:
        return f"【未知适配器】{target}"
    t = (ad.get("type") or "").lower()
    if t == "human":
        label = str(ad.get("label") or "")
        is_boss = str(ad.get("role_id") or "").lower() == "boss" or "大BOSS" in label or not label
        if is_boss:
            return "【大BOSS】这是人工拍板席位，不执行 AI 任务；请在群里 @大BOSS 等主理人拍板。"
        return f"【{label}】是人工中转岗（外包工），不自动执行任务；请走任务书/回报文档交接。"
    # R9.3（WB-SKILL-ROUTE-20261115-R9b）：仅工单派发 prompt 末尾追加【技能候选】引导行；
    # ad.ask_skill_candidate=false 可按岗关闭；群聊 prompt 前缀不匹配，不受影响。
    if (prompt.startswith("【工单派发】")
            and str(ad.get("ask_skill_candidate", True)).strip().lower() not in ("false", "0", "no", "off")):
        prompt += "\n4) 回件末尾加一行「【技能候选】有就写 3 行（做什么 / 关键步骤 / 坑），没有就写“无”。」"
    if t == "workbuddy":
        return workbuddy_chat(cfg, prompt, model, ad)
    if t == "coze":
        return coze_chat(cfg, prompt, ad)
    if t == "demo":
        return demo_chat(cfg, prompt)
    if t == "process":
        return process_chat(cfg, ad, prompt, model)
    if t == "openai":
        return openai_chat(cfg, ad, prompt, model)
    if t == "a2a":
        return a2a_chat(cfg, ad, prompt, model)
    if t == "webhook":
        return webhook_chat(cfg, ad, prompt, model)
    if t == "pull":
        return pull_chat(cfg, ad, prompt, model)
    return f"【适配器类型未支持】{target} type={t}"


def build_context_prompt(prompt: str, context: list | None) -> str:
    if not context:
        return prompt
    lines = []
    for m in context[-8:]:
        role = "用户" if m.get("role") == "user" else "AI"
        content = (m.get("content") or "").strip()
        if len(content) > 1200:
            content = content[:1200] + "…"
        if content:
            lines.append(f"{role}：{content}")
    if not lines:
        return prompt
    return "以下是本会话最近的对话（用于保持上下文）：\n" + "\n".join(lines) + "\n\n当前问题：\n" + prompt


# 背景注入脱敏：含密钥/Token 字样或疑似密钥串的行不注入
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9_\-\.=]{8,}|[A-Za-z0-9_\-]{40,})")


def _redact_secret(line: str) -> str:
    low = line.lower()
    if any(k in low for k in ("key", "token", "密钥", "密码", "password", "secret",
                              "credential", "api_key", "bearer", "access_key")):
        return "（已脱敏：含密钥/Token 字样，不注入）"
    if _SECRET_RE.search(line):
        return "（已脱敏：疑似密钥串，不注入）"
    return line


def build_project_background(project_dir: str) -> str:
    """读取项目目录下 00_环境.md（前 3000 字，脱敏） + 固定岗位边界/底线，生成背景块。
    任何读取失败都静默降级为空（调用方据此不注入）。"""
    block = []
    try:
        env_path = Path(project_dir) / "00_环境.md"
        if env_path.is_file():
            raw = env_path.read_text(encoding="utf-8", errors="replace")
            head = raw[:3000]
            kept = [x for ln in head.splitlines() if (x := _redact_secret(ln))]
            if kept:
                block.append("【项目固定背景 · 来自 00_环境.md（已脱敏，前 3000 字）】")
                block.append("\n".join(kept))
    except Exception:
        pass
    block.append(
        "【岗位边界与四条底线（自动注入，可关）】\n"
        "- 你只负责本岗位职责，不跨岗签门；最终验收（门①–⑤）始终由大BOSS 拍板，AI 不签门。\n"
        "- 四条底线：写码≠审、构建≠验、主理人唯一、闸口门禁（待验≠已验）。\n"
        "- 不许做的事：不自行创建/删除 AI、不绕过门禁、不输出或泄露 Key/Token/客户凭据、"
        "不执行超出本项目目录的命令。"
    )
    return "\n\n".join(block)


def _记忆前缀(cfg: dict, project_dir: str, query: str) -> str:
    """AI聊不停 记忆接入：召回块前缀；任何异常降级为空（memory 默认关闭时返回空）。"""
    if not 记忆:
        return ""
    try:
        block = 记忆.memory_recall(cfg, project_dir, query)
        return (block + "\n\n") if block else ""
    except Exception:  # noqa: BLE001
        return ""


def _记忆收尾():
    """退出时关闭所有 memsearch watch 子进程。"""
    if 记忆:
        try:
            记忆.memory_close_all()
        except Exception:  # noqa: BLE001
            pass


def run_chat_job(job_id: str, target: str, prompt: str, ticket_id: str | None, model: str | None = None,
                 context: list | None = None, attachments: list | None = None):
    cfg = load_config()
    ad = find_adapter(cfg, target) or {}
    label = ad.get("label") or target
    models = adapter_models(cfg, ad)
    if model and models:
        m = next((x for x in models if x["id"] == model), None)
        label = f"{label} · {m['name'] if m else model}"
    push_job_event(job_id, "status", f"已收到，正在调用 {label} …")
    final_prompt = build_context_prompt(prompt, context)
    project_dir = resolve_path((cfg.get("paths") or {}).get("project") or "")
    final_prompt = _记忆前缀(cfg, project_dir, prompt) + final_prompt
    if attachments:
        lines = [f"- {a.get('name')} ({a.get('mime')}, {a.get('size')} bytes, id={a.get('id')})" for a in attachments]
        final_prompt += "\n\n附件（仅本地保存；是否/如何识别由模型与适配器决定）：\n" + "\n".join(lines)
    try:
        reply = sanitize(dispatch_chat(cfg, target, final_prompt, model))
    except Exception as e:  # noqa: BLE001
        reply = sanitize(f"【执行异常】{e}")
    conn = db()
    with conn:
        conn.execute("UPDATE jobs SET status='done', updated_at=? WHERE id=?", (now_iso(), job_id))
        conn.execute(
            "INSERT INTO chat_messages(conversation_id, role, content, adapter, model, ticket_id, created_at) VALUES(?,?,?,?,?,?,?)",
            (target, "assistant", reply, target, model if models else None, ticket_id, now_iso()),
        )
    conn.close()
    if 记忆:
        try:
            记忆.memory_write(cfg, project_dir, prompt, reply, target, "chat")
        except Exception:  # noqa: BLE001
            pass
    push_job_event(job_id, "reply", reply)
    push_job_event(job_id, "done", "done")


def notify_boss(project_id: str, reason: str, ticket_id: str | None = None):
    """把需要人工拍板的事写到项目群，UI 显示为红色 @大BOSS 提醒。

    编排接入 v1：同 project_id + ticket_id + gate + reason 在 60s 内只发一次
    （gate 从 ticket_id 对应工单读取，读不到则空串）；调用方式不变。
    """
    gate = ""
    if ticket_id:
        try:
            c0 = db()
            tr = c0.execute("SELECT gate FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            c0.close()
            gate = (tr["gate"] if tr else "") or ""
        except Exception:
            gate = ""
    key = (project_id or "", ticket_id or "", gate, reason)
    now_ts = time.time()
    with _NOTIFY_LOCK:
        for k, ts in list(_NOTIFY_DEDUP.items()):
            if now_ts - ts > _NOTIFY_WINDOW_S:
                _NOTIFY_DEDUP.pop(k, None)
        prev = _NOTIFY_DEDUP.get(key)
        if prev is not None and now_ts - prev < _NOTIFY_WINDOW_S:
            return
        _NOTIFY_DEDUP[key] = now_ts
    conn = db()
    with conn:
        conn.execute(
            "INSERT INTO group_messages(project_id,role,content,adapter,mentions,created_at) VALUES(?,?,?,?,?,?)",
            (project_id, "alert", f"@大BOSS 请拍板：{reason}", "boss", json.dumps(["boss"], ensure_ascii=False), now_iso()),
        )
    conn.close()


def _envmd_notice(conn, pid: str, project_dir: str) -> bool:
    """C1（2026-11-13）：项目目录缺 00_环境.md 时，群聊提醒一次（meta 持久去重，不建文件）。"""
    d = (project_dir or "").strip()
    if not d:
        return False
    try:
        if not os.path.isdir(d) or os.path.exists(os.path.join(d, "00_环境.md")):
            return False
    except Exception:
        return False
    key = "envmd_notice:" + pid
    try:
        if conn.execute("SELECT 1 FROM meta WHERE k=?", (key,)).fetchone():
            return False
    except Exception:
        return False
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (key, now_iso()))
            conn.execute(
                "INSERT INTO group_messages(project_id,role,content,adapter,mentions,created_at) VALUES(?,?,?,?,?,?)",
                (pid, "system",
                 "【平台提醒】项目目录缺少 00_环境.md（该文件只能由人编写）。AI 读不到项目固定背景，"
                 "可能按默认口径执行；建议补写后再派单。补写后本提醒保持静默，不再重复。",
                 "platform", json.dumps(["boss"], ensure_ascii=False), now_iso()),
            )
        return True
    except Exception:
        return False


def run_group_job(job_id: str, project_id: str, target: str, prompt: str, attachments: list | None = None):
    cfg = load_config()
    conn = db()
    prow = conn.execute("SELECT dir, inject_profile FROM projects WHERE id=?", (project_id,)).fetchone()
    project_dir = (prow["dir"] if prow else "") or ""
    inject_profile = bool((prow["inject_profile"] if prow else 1))
    if project_dir:
        cfg["paths"] = dict(cfg.get("paths") or {})
        cfg["paths"]["project"] = project_dir
    conn.close()
    ad = find_adapter(cfg, target) or {}
    label = ad.get("label") or target
    push_job_event(job_id, "status", f"群聊中，正在调用 {label} …")
    conn = db()
    ctx = [dict(r) for r in conn.execute(
        "SELECT role,content FROM group_messages WHERE project_id=? ORDER BY id DESC LIMIT 8", (project_id,)).fetchall()]
    conn.close()
    ctx.reverse()
    context_block = build_context_prompt(prompt, ctx)
    prefix = (
        "你在项目群聊里。群里的「我」就是用户/老板（大BOSS），你的回复会直接显示在群聊，"
        "不需要调用飞书/钉钉/微信等外部 IM，也不要说「无法@用户」或「发不出去」。\n"
        "用户让你 @ 某人或发一句话，直接在回复里写出 `@某人` 和要发的内容即可。\n"
        "遇到需要拍板、死循环、模型超时/额度不足，写 `@大BOSS` 并停下等拍板，不要自行越过。"
    )
    background = ""
    if inject_profile and project_dir:
        background = build_project_background(project_dir)
    final_prompt = prefix
    if background:
        final_prompt += "\n\n" + background
    final_prompt += "\n\n" + context_block
    if attachments:
        final_prompt += "\n\n附件（仅本地保存；是否/如何识别由模型与适配器决定）：\n" + "\n".join(
            f"- {a.get('name')} ({a.get('mime')}, {a.get('size')} bytes, id={a.get('id')})" for a in attachments)
    if project_dir:
        final_prompt += f"\n\n当前项目目录：{project_dir}\n如用户要求，请先读取该目录下的 S4 冻结产物 / 00_环境.md，再执行。"
    final_prompt += "\n\n遇到需要主理人拍板、死循环、模型超时/额度不足等突发状态，必须先 @大BOSS 并停下等拍板，不要自行越过。"
    final_prompt = _记忆前缀(cfg, project_dir, prompt) + final_prompt
    try:
        reply = sanitize(dispatch_chat(cfg, target, final_prompt))
        err = None
    except Exception as e:  # noqa: BLE001
        reply = sanitize(f"【执行异常】{e}")
        err = str(e)
    if err or re.search(r"(超时|timeout|额度|quota|insufficient|余额|402)", reply, re.I):
        notify_boss(project_id, f"{label} 执行异常，需主理人判断：{(reply or err)[:160]}")
    conn = db()
    with conn:
        conn.execute("INSERT INTO group_messages(project_id,role,content,adapter,created_at) VALUES(?,?,?,?,?)",
                     (project_id, "assistant", reply, target, now_iso()))
        conn.execute("UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?",
                     ("error" if err else "done", err, now_iso(), job_id))
    conn.close()
    if 记忆:
        try:
            记忆.memory_write(cfg, project_dir, prompt, reply, target, "group")
        except Exception:  # noqa: BLE001
            pass
    push_job_event(job_id, "reply", reply)
    push_job_event(job_id, "done", "done")


def push_job_event(job_id: str, typ: str, content: str):
    conn = db()
    with conn:
        conn.execute("INSERT INTO job_events(job_id, at, type, content) VALUES(?,?,?,?)", (job_id, now_iso(), typ, content))
    conn.close()


# ---------------- HTTP ----------------

def ticket_row(conn, rid: str):
    r = conn.execute("SELECT * FROM tickets WHERE id=?", (rid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["events"] = [dict(x) for x in conn.execute("SELECT * FROM events WHERE ticket_id=? ORDER BY id", (rid,)).fetchall()]
    d["signoffs"] = [dict(x) for x in conn.execute("SELECT * FROM signoffs WHERE ticket_id=? ORDER BY id", (rid,)).fetchall()]
    return d


def project_public(conn, pid: str):
    r = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["members"] = [x["adapter_id"] for x in conn.execute(
        "SELECT adapter_id FROM project_members WHERE project_id=? ORDER BY added_at", (pid,)).fetchall()]
    d["member_count"] = len(d["members"])
    return d


def project_list(conn):
    return [project_public(conn, r["id"]) for r in conn.execute("SELECT id FROM projects ORDER BY created_at").fetchall()]


FEED_LABELS = {
    "intake_seen": "派单台收到工单",
    "dispatch_intent": "准备派单",
    "dispatch_ok": "已派单",
    "reply_seen": "已回件",
    "gate_passed": "门禁已签",
    "dispatch_fail": "派单失败",
    "model_switch": "切换模型",
    "emergency_stop": "急停",
}


def adapter_label(aid: str) -> str:
    """执行端 id → 界面显示名（配置里的 label），查不到就原样返回。"""
    if not aid:
        return ""
    try:
        ad = (load_config().get("adapters") or {}).get(aid) or {}
        return str(ad.get("label") or aid)
    except Exception:  # noqa: BLE001
        return str(aid)


def project_feed(conn, pid: str, limit: int = 60):
    """群消息：只给自动派单的摘要，不返回 AI 正文/备注。"""
    rows = conn.execute(
        "SELECT e.at,e.kind,e.actor,e.detail,t.id ticket_id,t.title,t.status,t.executor,t.gate "
        "FROM events e JOIN tickets t ON t.id=e.ticket_id "
        "WHERE t.project_id=? AND e.kind IN ('intake_seen','dispatch_intent','dispatch_ok','reply_seen','gate_passed','dispatch_fail','emergency_stop') "
        "ORDER BY e.id DESC LIMIT ?",
        (pid, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        kind = r["kind"]
        actor = r["actor"] or ""
        ex = adapter_label(r["executor"] or "")
        # 派单/收单都要写明两头是谁（v2.7）
        if kind == "intake_seen":
            di = actor if actor and actor not in ("桌面端", "派发器") else "主理人"
            text = f"{di}派单 → 派单台收单"
        elif kind == "dispatch_intent":
            text = f"派单台准备派单 → {ex or '执行端'}"
        elif kind == "dispatch_ok":
            text = f"派单台已派单 → {ex or '执行端'}收单"
        elif kind == "reply_seen":
            text = f"{ex or '执行端'}交单 → 主理人收单"
        elif kind == "gate_passed":
            gate = r["gate"] or ""
            text = f"主理人签收 → {ex or '执行端'}完工" + (f"（{gate}）" if gate else "")
        elif kind == "dispatch_fail":
            text = f"派单台派单失败 → {ex or '执行端'}未收单"
        elif kind == "emergency_stop":
            text = f"{actor or '主理人'}急停 → {ex or '执行端'}停止"
        else:
            text = FEED_LABELS.get(kind, "状态更新")
        who = r["executor"] or r["actor"] or "派单台"
        out.append({"at": r["at"], "kind": kind, "who": who, "text": text,
                    "ticket_id": r["ticket_id"], "title": r["title"],
                    "status": r["status"], "executor": r["executor"], "gate": r["gate"]})
    return out


def load_role_catalog():
    try:
        data = json.loads(ROLE_CATALOG_PATH.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {"layers": [], "rules": []}
    except Exception:
        return {"layers": [], "rules": []}


def project_role_assignments(conn, pid: str):
    out = {}
    for r in conn.execute("SELECT role_id,adapter_id FROM project_roles WHERE project_id=? ORDER BY role_id", (pid,)).fetchall():
        out.setdefault(r["role_id"], []).append(r["adapter_id"])
    return out


def validate_role_assignments(cfg: dict, catalog: dict, members: set, assignments: dict) -> list:
    roles = {}
    for layer in catalog.get("layers", []):
        for r in layer.get("roles", []):
            roles[r.get("id")] = r
    adapters = cfg.get("adapters") or {}
    errors = []
    clean = {}
    if not isinstance(assignments, dict):
        return ["assignments 必须是 {岗位: [AI]}"], {}
    for rid, aids in assignments.items():
        r = roles.get(rid)
        if not r:
            errors.append(f"未知岗位：{rid}")
            continue
        if isinstance(aids, str):
            aids = [aids]
        if not isinstance(aids, list):
            errors.append(f"{r.get('name', rid)} 的 AI 列表格式不对")
            continue
        aids = [str(x) for x in aids if str(x).strip()]
        if r.get("unique") and len(aids) > 1:
            errors.append(f"{r.get('name', rid)} 只能 1 个")
        for aid in aids:
            if aid not in members:
                errors.append(f"{aid} 不在项目成员里（先加入项目群）")
                continue
            ad = adapters.get(aid) or {}
            human = (ad.get("type") or "").lower() == "human"
            if r.get("human_only") and not human:
                errors.append(f"{r.get('name', rid)} 必须由人工担任")
            if not r.get("human_only") and human:
                errors.append(f"{r.get('name', rid)} 不是人工岗，不能填 {aid}")
            if aid not in clean.setdefault(rid, []):
                clean.setdefault(rid, []).append(aid)
    # 同一 AI 的角色集合冲突
    aid_roles = {}
    for rid, aids in clean.items():
        for aid in aids:
            aid_roles.setdefault(aid, set()).add(rid)
    def has(aid, pred):
        return any(pred(roles.get(rid, {})) for rid in aid_roles.get(aid, set()))
    for aid, rids in aid_roles.items():
        if has(aid, lambda r: r.get("can_write")) and has(aid, lambda r: r.get("can_audit")):
            errors.append(f"{aid} 同时承担写码和审核岗，违反分离")
        if has(aid, lambda r: r.get("can_build")) and has(aid, lambda r: r.get("can_accept")):
            errors.append(f"{aid} 同时承担构建和独立验收，违反分离")
        if "lead" in rids and "independent_acceptor" in rids:
            errors.append(f"{aid} 不能同时是主理人和独立验收人")
        if "independent_acceptor" in rids and (
            has(aid, lambda r: r.get("can_write")) or has(aid, lambda r: r.get("can_build"))):
            errors.append(f"{aid} 是独立验收人，不能同时写码/构建")
    return errors, clean


# ---------------- AI 岗位配置：新增 / 导出 / 默认岗位继承（R1–R3） ----------------

ROSTER_SCHEMA = "ai-roster/v1"
ROSTER_SOURCE = "AI聊不停"
ROSTER_FIELDS = ("id", "label", "type", "role_id", "post", "enabled", "default_model")
ROSTER_FORBIDDEN = (
    "sk-", "bearer ", "codebuddy_api_key", "api_key", "token", "cookie",
    "password", "secret", "authorization", "c:", "g:", "\\\\", "/home/",
    "/users/", "command", "cmd.exe", ".sqlite3",
)
ADAPTER_TYPES = {"process", "workbuddy", "coze", "demo", "human"}


def role_by_id(catalog, rid):
    for layer in (catalog.get("layers") or []):
        for r in (layer.get("roles") or []):
            if r.get("id") == rid:
                return r
    return None


def slugify_adapter_id(s):
    """稳定 id：小写 kebab-case；中文名称转空时由调用方补 ai-<hex>。"""
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")[:48]


def backup_config():
    """写 配置.json 前备份为 配置.json.bak.<时间戳>，同名追加序号。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.bak.{stamp}")
    n = 2
    while bak.exists():
        bak = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.bak.{stamp}-{n}")
        n += 1
    if CONFIG_PATH.exists():
        shutil.copy2(CONFIG_PATH, bak)
    return bak


def build_roster(cfg):
    """§3.2 固定格式：只取白名单字段，绝不导出 command / paths / Key。"""
    catalog = load_role_catalog()
    ais = []
    for aid, ad in (cfg.get("adapters") or {}).items():
        ais.append({
            "id": aid,
            "label": ad.get("label") or aid,
            "type": ad.get("type") or "",
            "role_id": ad.get("role_id") or "",
            "post": ad.get("post") or "",
            "enabled": is_enabled(ad),
            "default_model": ad.get("default_model"),
        })
    return {
        "schema": ROSTER_SCHEMA,
        "exported_at": now_iso(),
        "role_catalog_version": str(catalog.get("version") or ""),
        "source": ROSTER_SOURCE,
        "ais": ais,
    }


def scan_roster_text(text):
    """导出前兜底扫描：命中任一敏感串即拒绝导出并提示。"""
    low = str(text or "").lower()
    return [x for x in ROSTER_FORBIDDEN if x in low]


def plan_member_sync(conn, cfg, catalog, pid, wanted):
    """R3：只计算，不写表。返回 (errors, plan)。
    默认岗位空 → 继承；已有人 → 不覆盖；human_only 被 AI 占 → 拒绝；
    再把「现有编制 + 本次继承」整体交给 validate_role_assignments 做冲突校验。"""
    roles = {}
    for layer in (catalog.get("layers") or []):
        for r in (layer.get("roles") or []):
            roles[r.get("id")] = r
    current = {x["adapter_id"] for x in conn.execute(
        "SELECT adapter_id FROM project_members WHERE project_id=?", (pid,)).fetchall()}
    to_add = sorted(wanted - current)
    to_remove = sorted(current - wanted)
    assignments = project_role_assignments(conn, pid)
    clean = {rid: [a for a in aids if a in wanted] for rid, aids in assignments.items()}
    clean = {rid: aids for rid, aids in clean.items() if aids}
    adapters = cfg.get("adapters") or {}
    inherited, skipped, errors = {}, {}, []
    for aid in to_add:
        ad = adapters.get(aid) or {}
        rid = ad.get("role_id") or ""
        role = roles.get(rid)
        if not role:
            continue
        human = (ad.get("type") or "").lower() == "human"
        if role.get("human_only") and not human:
            errors.append(f"{aid} 的默认岗位「{role.get('name') or rid}」是人工岗，不能由 AI 担任")
            continue
        if human and not role.get("human_only"):
            continue
        holders = [a for a in clean.get(rid, []) if a in wanted]
        if holders:
            skipped[aid] = {"role_id": rid, "post": role.get("name") or "",
                            "reason": "岗位已有人，去岗位编制调整", "holders": holders}
            continue
        clean.setdefault(rid, []).append(aid)
        inherited[aid] = {"role_id": rid, "post": role.get("name") or "",
                          "label": ad.get("label") or aid}
    verr, _ = validate_role_assignments(cfg, catalog, wanted, clean)
    errors.extend(verr)
    return errors, {"to_add": to_add, "to_remove": to_remove,
                    "inherited": inherited, "skipped": skipped}


def apply_member_sync(conn, pid, plan):
    """整单原子写入：成员 + 继承岗位 + 项目时间戳，同一事务。"""
    now = now_iso()
    with conn:
        for aid in plan["to_remove"]:
            conn.execute("DELETE FROM project_members WHERE project_id=? AND adapter_id=?", (pid, aid))
            conn.execute("DELETE FROM project_roles WHERE project_id=? AND adapter_id=?", (pid, aid))
        for aid in plan["to_add"]:
            conn.execute("INSERT OR IGNORE INTO project_members(project_id,adapter_id,added_at) VALUES(?,?,?)",
                         (pid, aid, now))
        for aid, info in plan["inherited"].items():
            conn.execute("INSERT OR IGNORE INTO project_roles(project_id,role_id,adapter_id,created_at) VALUES(?,?,?,?)",
                         (pid, info["role_id"], aid, now))
        conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, pid))


def adapter_status(cfg: dict) -> dict:
    p = cfg["paths"]
    out = {}
    for aid, ad in cfg.get("adapters", {}).items():
        t = (ad.get("type") or "").lower()
        enabled = is_enabled(ad)
        label = ad.get("label") or aid
        models = adapter_models(cfg, ad)
        ready, detail, hint = False, "未配置", ""
        if t == "human":
            ready, detail, hint = True, str(ad.get("detail") or "人工拍板席位，不执行 AI 任务"), "人工"
        elif t == "workbuddy":
            key_path, ksrc = adapter_key_path(cfg, ad, "workbuddy")
            # R1-A：有 adapter 级 key_ref 只检查它；否则回落全局/env
            key = bool(read_secret(key_path)) if ksrc.startswith("adapter:") else bool(workbuddy_key(cfg))
            cli = resolve_path(p.get("workbuddy_cli", ""))
            cli_ok = Path(cli).exists()
            ready = enabled and key and cli_ok
            if ready:
                detail, hint = "Key 已配置，CLI 存在", "已配置"
            elif not enabled:
                detail, hint = "已禁用", "disabled"
            elif not key:
                detail = f"缺 API Key：{key_path} 不存在"
                hint = ("放回 Key 文件或改 " + ksrc) if ksrc.startswith("adapter:") \
                    else "放回 Key 文件或改 paths.workbuddy_key"
            else:
                detail, hint = f"缺 CLI：{cli}", "检查 paths.workbuddy_cli"
        elif t == "coze":
            token_path, tsrc = adapter_key_path(cfg, ad, "coze")
            tok = bool(read_secret(token_path))
            ready = enabled and tok
            detail = "Token 已配置" if tok else f"缺 API Token：{token_path} 不存在"
            hint = ("已配置" if tok else "检查 " + tsrc)
        elif t == "demo":
            ready, detail, hint = True, "可直接聊天", "本地演示"
        elif t == "process":
            raw = ad.get("command")
            first = ""
            if raw:
                first = shlex.split(raw)[0] if isinstance(raw, str) else str(raw[0])
                first = _fmt(first, cfg, "", "", resolve_path(ad.get("cwd") or p.get("project") or ""))
            exists = bool(first) and (Path(first).exists() or shutil.which(first))
            ready = enabled and exists
            detail = "CLI 可执行" if exists else "缺 command/可执行文件"
            hint = Path(first).name if first else "未配置"
        elif t == "openai":
            base = ad.get("base_url") or ""
            key = bool(ad.get("api_key_env") and os.environ.get(ad["api_key_env"]))
            if not key and ad.get("api_key_file"):
                key = bool(read_secret(resolve_path(p.get(ad["api_key_file"], ad["api_key_file"]))))
            ready = enabled and bool(base) and key
            detail = "base_url/Key 已配置" if ready else ("缺 base_url" if not base else "缺 API Key")
            hint = "已配置" if ready else "未配置"
        elif t in ("a2a", "webhook"):
            url = ad.get("url") or ""
            ready = enabled and bool(url)
            detail = "URL 已配置" if url else "缺 url"
            hint = "已配置" if url else "未配置"
        elif t == "pull":
            outbox = resolve_path(ad.get("outbox") or "")
            ready = enabled and bool(outbox)
            detail = "投递目录可用（不实时回复）" if outbox else "缺 outbox"
            hint = "已配置" if outbox else "未配置"
        else:
            ready = enabled
            detail = "未知类型：可只读接入（不会自动对话）"
        out[aid] = {
            "label": label, "post": ad.get("post") or label,
            "role_id": ad.get("role_id") or "",
            "type": t or "unknown", "enabled": enabled, "ready": ready,
            "detail": detail, "hint": hint, "models": models,
            "default_model": ad.get("default_model") or (models[0]["id"] if models else None),
            "chat": True, "refresh_models": t == "workbuddy",
        }
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "TicketDeck/0.1"

    def log_message(self, fmt, *args):  # quieter
        return

    def _deny(self, code: int, msg: str):
        self._json({"error": msg}, code)

    def _guard(self, auth: bool = False) -> bool:
        port = self.server.server_address[1]
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        host = self.headers.get("Host", "")
        if host not in allowed_hosts:
            self._deny(403, "forbidden host")
            return False
        origin = self.headers.get("Origin")
        if origin:
            allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            if origin not in allowed_origins:
                self._deny(403, "forbidden origin")
                return False
        if auth:
            token = self.headers.get("X-Deck-Token") or ""
            if not token:
                raw = self.headers.get("Cookie") or ""
                for part in raw.split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == "deck_token":
                        token = v
                        break
            if token != getattr(self.server, "deck_token", ""):
                # C8：可选外部 token（security.external_token_file / 环境变量指定文件）
                ext = getattr(self.server, "external_token", "") or ""
                if not ext or token != ext:
                    self._deny(403, "invalid token")
                    return False
        return True

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > 262144:
            raise ValueError("body too large")
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _static(self, path: str):
        rel = urllib.parse.unquote(path.lstrip("/"))
        if not rel:
            rel = "index.html"
        f = (WEB / rel).resolve()
        if not _is_relative_to(f, WEB) or not f.is_file():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype in ("application/javascript", "application/json") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("token", [""])[0] == getattr(self.server, "deck_token", ""):
            self.send_header("Set-Cookie", f"deck_token={self.server.deck_token}; Path=/; SameSite=Strict; HttpOnly")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        if p == "/api/health":
            if not self._guard(auth=False):
                return
        elif p.startswith("/api/"):
            if not self._guard(auth=True):
                return
        else:
            if not self._guard(auth=False):
                return
        try:
            if p == "/api/health":
                return self._json({"ok": True, "app": APP_TITLE, "version": APP_VERSION, "time": now_iso()})
            # ---- 外部编排接口 v1（只读；orchestration.enabled=false 时仍可探测，不返回 404/403） ----
            if p == "/api/orchestration/pending":
                q = urllib.parse.parse_qs(u.query)
                pid = (q.get("project_id") or [""])[0].strip()
                limit = _parse_limit((q.get("limit") or ["50"])[0], 50, 200, 1)
                where = "status IN ('待验','失败','待处理')"
                args = []
                if pid:
                    where += " AND project_id=?"
                    args.append(pid)
                conn = db()
                counts = {"待验": 0, "失败": 0, "待处理": 0}
                for r in conn.execute(f"SELECT status, COUNT(*) AS c FROM tickets WHERE {where} GROUP BY status", args):
                    if r["status"] in counts:
                        counts[r["status"]] = r["c"]
                items = [dict(r) for r in conn.execute(
                    f"SELECT id,title,status,executor,gate,project_id,updated_at,next_action "
                    f"FROM tickets WHERE {where} ORDER BY updated_at DESC LIMIT ?", args + [limit])]
                conn.close()
                return self._json({"ok": True, "counts": counts, "items": items})
            if p == "/api/orchestration/outbox":
                q = urllib.parse.parse_qs(u.query)
                after = _parse_limit((q.get("after") or ["0"])[0], 0, 2**31 - 1, 0)
                default_limit = _orch_cfg(load_config())[1]
                limit = _parse_limit((q.get("limit") or [str(default_limit)])[0], default_limit, 500, 1)
                conn = db()
                items = [dict(r) for r in conn.execute(
                    "SELECT e.id AS seq, e.at AS at, e.kind AS kind, e.actor AS actor, e.detail AS detail, "
                    "e.ticket_id AS ticket_id, COALESCE(t.status,'') AS status, COALESCE(t.gate,'') AS gate, "
                    "COALESCE(t.project_id,'') AS project_id, COALESCE(t.title,'') AS title "
                    "FROM events e LEFT JOIN tickets t ON t.id=e.ticket_id "
                    "WHERE e.id > ? ORDER BY e.id ASC LIMIT ?", (after, limit))]
                conn.close()
                return self._json({"ok": True, "items": items,
                                   "next": items[-1]["seq"] if items else after})
            if p.startswith("/api/orchestration/tickets/"):
                rid = urllib.parse.unquote(p.split("/api/orchestration/tickets/", 1)[1].split("/")[0])
                conn = db()
                r = conn.execute("SELECT * FROM tickets WHERE id=?", (rid,)).fetchone()
                if not r:
                    conn.close()
                    return self._json({"error": "not found"}, 404)
                ticket = dict(r)
                evs = [dict(x) for x in conn.execute(
                    "SELECT * FROM events WHERE ticket_id=? ORDER BY id DESC LIMIT 50", (rid,))]
                evs.reverse()
                signoffs = [dict(x) for x in conn.execute(
                    "SELECT * FROM signoffs WHERE ticket_id=? ORDER BY id", (rid,))]
                conn.close()
                return self._json({"ok": True, "ticket": ticket, "events": evs, "signoffs": signoffs})
            if p == "/api/onboarding":
                try:
                    cfg = load_config()
                    rep = 引导.build_report(cfg, CONFIG_PATH, DATA, ROLE_CATALOG_PATH, APP_VERSION)
                except Exception as e:  # noqa: BLE001
                    return self._json({"ok": False, "error": f"自检失败：{e}"}, 500)
                return self._json(rep)
            if p == "/api/state":
                cfg = load_config()
                conn = db()
                tickets = [dict(r) for r in conn.execute("SELECT * FROM tickets ORDER BY updated_at DESC").fetchall()]
                stats = {s: 0 for s in STATUSES}
                for t in tickets:
                    stats[t["status"]] = stats.get(t["status"], 0) + 1
                meta = {r["k"]: r["v"] for r in conn.execute("SELECT * FROM meta").fetchall()}
                evs = [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 20").fetchall()]
                projects = project_list(conn)
                conn.close()
                _tc = (cfg.get("skills") or {}).get("tripcard") or {}
                _skills_pub = {"tripcard": {"enabled": bool(_tc.get("enabled", False)),
                                            "keywords": list(_tc.get("keywords") or [])}}
                return self._json({"app": APP_TITLE, "version": APP_VERSION, "stats": stats, "tickets": tickets,
                                   "adapters": adapter_status(cfg), "meta": meta, "recent_events": evs,
                                   "projects": projects,
                                   "chat": cfg.get("chat", {}),
                                   "skills": _skills_pub,
                                   "models": {"workbuddy": get_workbuddy_models(cfg),
                                              "workbuddy_default": cfg["adapters"].get("workbuddy", {}).get("default_model", "hy4free")}})
            if p == "/api/projects":
                conn = db()
                rs = project_list(conn)
                conn.close()
                return self._json(rs)
            if p.startswith("/api/projects/") and p.endswith("/feed"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                q = urllib.parse.parse_qs(u.query)
                try:
                    limit = max(1, min(200, int((q.get("limit") or ["60"])[0])))
                except ValueError:
                    limit = 60
                feed = project_feed(conn, pid, limit)
                conn.close()
                return self._json({"project_id": pid, "feed": feed})
            if p.startswith("/api/projects/") and p.endswith("/roles"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                members = [x["adapter_id"] for x in conn.execute(
                    "SELECT adapter_id FROM project_members WHERE project_id=?", (pid,)).fetchall()]
                assignments = project_role_assignments(conn, pid)
                conn.close()
                return self._json({"project_id": pid, "catalog": load_role_catalog(),
                                   "members": members, "assignments": assignments})
            if p.startswith("/api/projects/") and p.endswith("/messages"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                q = urllib.parse.parse_qs(u.query)
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                msgs = []
                # 缺陷1：真正读取 limit 参数（默认 120，上限 1000，非数字回落 120，不报 500）
                glimit = _int_param(q, "limit", 120, 1000, 120)
                for r in conn.execute("SELECT * FROM group_messages WHERE project_id=? ORDER BY id DESC LIMIT ?", (pid, int(glimit))).fetchall():
                    d = dict(r)
                    try:
                        d["mentions"] = json.loads(d.get("mentions") or "[]")
                    except Exception:
                        d["mentions"] = []
                    d["type"] = "chat"
                    d["at"] = d["created_at"]
                    msgs.append(d)
                # 系统事件同样参与时间排序；feed 上限与群聊上限脱钩，单独给足
                FEED_LIMIT = 40
                for it in project_feed(conn, pid, FEED_LIMIT):
                    it = dict(it)
                    it["type"] = "system"
                    msgs.append(it)
                conn.close()
                # 旧→新排序；system 与 chat 都按时间
                msgs.sort(key=lambda x: x.get("at") or "")
                # 缺陷2：按实际 limit 计算截断，不再写死 160（否则把 limit 提到 1000 又会被砍回 160）
                return self._json({"project_id": pid, "messages": msgs[-(glimit + FEED_LIMIT):]})
            if p.startswith("/api/projects/"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].split("/")[0])
                conn = db()
                d = project_public(conn, pid)
                conn.close()
                if not d:
                    return self._json({"error": "project not found"}, 404)
                return self._json(d)
            if p.startswith("/api/attachments/"):
                aid = urllib.parse.unquote(p.split("/api/attachments/", 1)[1].split("/")[0])
                conn = db()
                r = conn.execute("SELECT * FROM attachments WHERE id=?", (aid,)).fetchone()
                conn.close()
                if not r:
                    return self._json({"error": "not found"}, 404)
                path = Path(r["path"])
                if not path.is_file():
                    return self._json({"error": "file missing"}, 404)
                data = path.read_bytes()
                mime = r["mime"] or "application/octet-stream"
                inline = mime.startswith("image/")
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                disp = "inline" if inline else "attachment"
                self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{urllib.parse.quote(r['name'])}")
                self.end_headers()
                self.wfile.write(data)
                return
            if p == "/api/tickets":
                conn = db()
                rs = [dict(r) for r in conn.execute("SELECT * FROM tickets ORDER BY updated_at DESC").fetchall()]
                conn.close()
                return self._json(rs)
            if p.startswith("/api/tickets/"):
                rid = urllib.parse.unquote(p.split("/api/tickets/", 1)[1].split("/")[0])
                conn = db()
                d = ticket_row(conn, rid)
                conn.close()
                if not d:
                    return self._json({"error": "not found"}, 404)
                return self._json(d)
            if p == "/api/chat/history":
                q = urllib.parse.parse_qs(u.query)
                target = (q.get("target") or ["workbuddy"])[0]
                conn = db()
                rs = [dict(r) for r in conn.execute(
                    "SELECT * FROM chat_messages WHERE conversation_id=? ORDER BY id", (target,)).fetchall()]
                conn.close()
                return self._json(rs)
            if p == "/api/search/messages":
                qry = urllib.parse.parse_qs(u.query)
                scope = (qry.get("scope") or [""])[0]
                if scope not in ("chat", "project"):
                    return self._json({"error": "scope 必须是 chat 或 project"}, 400)
                target = (qry.get("target") or [""])[0]
                project_id = (qry.get("project_id") or [""])[0]
                if scope == "chat" and not target:
                    return self._json({"error": "scope=chat 时必须提供 target（会话 id）"}, 400)
                if scope == "project" and not project_id:
                    return self._json({"error": "scope=project 时必须提供 project_id"}, 400)
                q_text = (qry.get("q") or [""])[0]
                role = (qry.get("role") or [""])[0]
                since = (qry.get("since") or [""])[0]
                until = (qry.get("until") or [""])[0]
                limit = _int_param(qry, "limit", 50, 200, 50)
                offset = _int_param(qry, "offset", 0, None, 0)
                if offset < 0:
                    offset = 0
                conn = db()
                try:
                    items = _collect_search(conn, scope, target, project_id, q_text, role, since, until)
                finally:
                    conn.close()
                # total 是过滤后的命中总数，不是当页返回条数
                total = len(items)
                items.sort(key=lambda x: x.get("at") or "")
                page = items[offset:offset + limit]
                out = []
                for it in page:
                    it = dict(it)
                    it.pop("_bucket", None)
                    out.append(it)
                return self._json({"ok": True, "scope": scope, "total": total,
                                   "limit": limit, "offset": offset, "items": out})
            if p == "/api/timeline":
                qry = urllib.parse.parse_qs(u.query)
                scope = (qry.get("scope") or [""])[0]
                if scope not in ("chat", "project"):
                    return self._json({"error": "scope 必须是 chat 或 project"}, 400)
                target = (qry.get("target") or [""])[0]
                project_id = (qry.get("project_id") or [""])[0]
                if scope == "chat" and not target:
                    return self._json({"error": "scope=chat 时必须提供 target"}, 400)
                if scope == "project" and not project_id:
                    return self._json({"error": "scope=project 时必须提供 project_id"}, 400)
                since = (qry.get("since") or [""])[0]
                until = (qry.get("until") or [""])[0]
                q_text = (qry.get("q") or [""])[0]
                role = (qry.get("role") or [""])[0]
                conn = db()
                try:
                    items = _collect_search(conn, scope, target, project_id, q_text, role, since, until)
                finally:
                    conn.close()
                # 与搜索接口共用同一套过滤口径：按天分组后 count 之和必等于同条件 search 的 total
                days = {}
                for it in items:
                    d = _day_of(it.get("at"))
                    if not d:
                        continue
                    b = it.get("_bucket") or "assistant"
                    e = days.setdefault(d, {"date": d, "count": 0, "user": 0, "assistant": 0, "system": 0})
                    e["count"] += 1
                    if b in e:
                        e[b] += 1
                return self._json({"ok": True, "days": [days[k] for k in sorted(days)]})
            if p.startswith("/api/jobs/") and p.endswith("/events"):
                job_id = p.split("/api/jobs/", 1)[1].rsplit("/", 1)[0]
                return self._sse(job_id)
            if p == "/api/role-catalog":
                return self._json(load_role_catalog())
            if p == "/api/roster":
                roster = build_roster(load_config())
                hits = scan_roster_text(json.dumps(roster, ensure_ascii=False, indent=2))
                if hits:
                    return self._json({"error": "导出被拒绝：检测到敏感字段或本机路径",
                                       "hits": hits}, 400)
                return self._json(roster)
            if p == "/api/adapters":
                return self._json(adapter_status(load_config()))
            if p == "/api/models":
                cfg = load_config()
                wb = cfg["adapters"].get("workbuddy", {})
                return self._json({"workbuddy": get_workbuddy_models(cfg),
                                   "default": wb.get("default_model", "hy4free"),
                                   "cached_at": (json.loads(MODELS_CACHE.read_text(encoding="utf-8")).get("at") if MODELS_CACHE.exists() else None)})
            if p == "/api/coze/bot":
                return self._json(coze_bot_info())
            return self._static(p)
        except BrokenPipeError:
            return
        except Exception as e:  # noqa: BLE001
            try:
                self._json({"error": str(e)}, 500)
            except Exception:
                pass

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        if not self._guard(auth=True):
            return
        if p == "/api/attachments":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json({"error": "bad content-length"}, 400)
            if n <= 0 or n > MAX_ATTACH:
                return self._json({"error": f"文件大小需在 1B–{MAX_ATTACH // 1024 // 1024}MB"}, 413)
            try:
                raw = self.rfile.read(n)
            except Exception:
                return self._json({"error": "读取失败"}, 400)
            name = urllib.parse.unquote(self.headers.get("X-Filename") or "file")
            mime = (self.headers.get("Content-Type") or "").strip() or (mimetypes.guess_type(name)[0] or "application/octet-stream")
            if mime.startswith("multipart/"):
                mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            rec = save_attachment(name, mime, raw)
            return self._json({"ok": True, "attachment": rec})
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._json({"error": "Content-Type must be application/json"}, 415)
        try:
            if int(self.headers.get("Content-Length") or 0) > 262144:
                return self._json({"error": "body too large"}, 413)
        except ValueError:
            return self._json({"error": "bad content-length"}, 400)
        body = self._read_json()
        try:
            if p == "/api/onboarding/done":
                cfg = load_config()
                cfg2 = 引导.mark(cfg)
                save_config(cfg2)
                return self._json({"ok": True, "onboarded": True,
                                   "onboarded_at": cfg2.get("onboarded_at")})
            if p == "/api/emergency-stop":
                project_id = str(body.get("project_id") or "").strip() or None
                killed = False
                if DISPATCHER_PID.exists():
                    try:
                        dpid = int(DISPATCHER_PID.read_text(encoding="utf-8").strip())
                    except Exception:
                        dpid = 0
                    if dpid and dpid != os.getpid():
                        try:
                            if os.name == "nt":
                                subprocess.run(["taskkill", "/PID", str(dpid), "/T", "/F"], capture_output=True, **_child_kwargs())
                            else:
                                os.kill(dpid, signal.SIGTERM)
                            killed = True
                        except Exception:
                            killed = False
                for lp in (DISPATCHER_PID, DISPATCHER_LOCK):
                    try:
                        lp.unlink(missing_ok=True)
                    except Exception:
                        pass
                conn = db()
                now = now_iso()
                with conn:
                    if project_id:
                        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                            conn.close()
                            return self._json({"error": "project not found"}, 404)
                        conn.execute("UPDATE projects SET status='stopped', updated_at=? WHERE id=?", (now, project_id))
                        rows = conn.execute("SELECT id FROM tickets WHERE project_id=? AND status='派发中'", (project_id,)).fetchall()
                    else:
                        rows = conn.execute("SELECT id FROM tickets WHERE status='派发中'").fetchall()
                        conn.execute("UPDATE projects SET status='stopped', updated_at=?", (now,))
                    marked = 0
                    for r in rows:
                        conn.execute("UPDATE tickets SET status='失败', next_action=?, updated_at=? WHERE id=?",
                                     ("急停：等待人工复核", now, r["id"]))
                        add_event(conn, r["id"], "emergency_stop", "主理人", "项目急停：派发器终止，进行中单据转失败")
                        marked += 1
                project = project_public(conn, project_id) if project_id else None
                conn.close()
                if project_id:
                    notify_boss(project_id, f"项目已急停，进行中 {marked} 张单转失败，请判断是否回滚/重派")
                return self._json({"ok": True, "killed": killed, "marked_failed": marked, "project": project})
            if p == "/api/adapters":
                # R1 新增 AI：id 稳定、岗位校验、Key 只收路径引用、写前备份配置
                label = str(body.get("label") or "").strip()[:60]
                typ = str(body.get("type") or "").strip().lower()
                rid = str(body.get("role_id") or "").strip()
                if not label:
                    return self._json({"error": "名称/label 不能为空"}, 400)
                if typ not in ADAPTER_TYPES:
                    return self._json({"error": f"类型不支持：{typ}（仅 process/workbuddy/coze/demo/human）"}, 400)
                role = role_by_id(load_role_catalog(), rid)
                if not role:
                    return self._json({"error": f"默认岗位不在岗位目录：{rid}"}, 400)
                human = typ == "human"
                if role.get("human_only") and not human:
                    return self._json({"error": f"「{role.get('name')}」是人工岗，只能 type=human"}, 400)
                if human and not role.get("human_only"):
                    return self._json({"error": "type=human 只能选人工岗（boss / independent_acceptor）"}, 400)
                aid = slugify_adapter_id(body.get("id") or label) or ("ai-" + uuid.uuid4().hex[:8])
                if aid in (load_config().get("adapters") or {}):
                    return self._json({"error": f"适配器 ID 已存在：{aid}"}, 400)
                cmd = body.get("command")
                if typ == "process":
                    if not cmd:
                        return self._json({"error": "process 类型必须填 command"}, 400)
                    if isinstance(cmd, str):
                        if any(ch in cmd for ch in " ;&|><`$\n"):
                            return self._json({"error": "command 不许填 Shell 拼接字符串，请用命令数组"}, 400)
                        cmd = [cmd]
                    if not isinstance(cmd, list) or not cmd or not all(
                            isinstance(x, str) and x.strip() for x in cmd):
                        return self._json({"error": "command 必须是非空字符串数组"}, 400)
                    cmd = [str(x).strip()[:500] for x in cmd[:20]]
                model = str(body.get("default_model") or "").strip()[:60]
                key_path = str(body.get("key_path") or "").strip()[:300]
                if key_path:
                    low = key_path.lower()
                    if any(x in low for x in ("sk-", "bearer ", "api_key", "codebuddy_api_key",
                                              "password", "token=")):
                        return self._json({"error": "Key/Token 只允许填本机密钥文件路径，禁止明文"}, 400)
                    if not (re.match(r"^[A-Za-z]:[\\/]", key_path) or key_path.startswith("/")
                            or (key_path.startswith("{") and key_path.endswith("}"))):
                        return self._json({"error": "Key 必须是本机密钥文件绝对路径或 {paths} 占位符"}, 400)
                ad = {"type": typ, "label": label, "role_id": rid,
                      "post": role.get("name") or rid,
                      "enabled": is_enabled({"enabled": body.get("enabled", True)})}
                if model:
                    ad["default_model"] = model
                enc = str(body.get("encoding") or "").strip().lower()
                if enc and enc not in ("utf-8", "utf8", "latin-1", "latin1", "ascii", "gbk"):
                    return self._json({"error": f"不支持的 encoding：{enc}"}, 400)
                if enc:
                    ad["encoding"] = enc
                if typ == "process":
                    ad["command"] = cmd
                    ad["timeout_s"] = 300
                raw = {}
                if CONFIG_PATH.exists():
                    try:
                        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
                    except Exception:
                        raw = {}
                # R1-B：按 validate_process_adapter 的同一口径判断 command[0] 是否在白名单；
                # 未勾选「允许执行」且不在白名单 → 400，不写任何配置
                whitelisted = False
                if typ == "process":
                    cfg_now = load_config()
                    sec = cfg_now.get("security") or {}
                    allowed = {resolve_path(v) for v in (cfg_now.get("paths") or {}).values()
                               if isinstance(v, str)}
                    allowed |= set(sec.get("process_allowed_executables") or [])
                    first = _fmt(cmd[0], cfg_now, "", "", "")
                    if first not in allowed:
                        if not is_enabled({"enabled": body.get("allow_executable", False)}):
                            return self._json({
                                "error": (f"命令「{first}」不在执行白名单；请勾选"
                                          f"「允许执行这个程序（加入执行白名单）」或手工加入 "
                                          f"security.process_allowed_executables"
                                          f"（错误码 process_not_whitelisted）"),
                                "code": "process_not_whitelisted", "command": first}, 400)
                        wl = raw.setdefault("security", {}).setdefault(
                            "process_allowed_executables", [])
                        if first not in wl:
                            wl.append(first)
                        whitelisted = True
                # R1-A：key_path 存为 adapter 级引用（不再只登记 paths）
                if key_path:
                    ref = "key_" + aid
                    raw.setdefault("paths", {})[ref] = key_path
                    if typ == "coze":
                        ad["token_ref"] = ref
                    elif typ == "process":
                        ad["key_ref"] = ref
                        ad["secret_env"] = {"CODEBUDDY_API_KEY": ref}
                    else:
                        ad["key_ref"] = ref
                raw.setdefault("adapters", {})[aid] = ad
                bak = backup_config()
                save_config(raw)
                return self._json({"ok": True, "id": aid,
                                   "adapter": adapter_status(load_config()).get(aid, {}),
                                   "backup": bak.name, "whitelisted": whitelisted})
            if p.startswith("/api/adapters/") and p.endswith("/model"):
                aid = urllib.parse.unquote(p.split("/api/adapters/", 1)[1].rsplit("/", 1)[0])
                model = str(body.get("model") or "").strip()[:80]
                cfg = load_config()
                ad = (cfg.get("adapters") or {}).get(aid)
                if not ad:
                    return self._json({"error": f"未知适配器：{aid}"}, 404)
                models = adapter_models(cfg, ad)
                if not models:
                    return self._json({"error": "这个 AI 没有可选模型"}, 400)
                if not model or model not in {m["id"] for m in models}:
                    return self._json({"error": f"不支持的模型：{model}"}, 400)
                raw = {}
                if CONFIG_PATH.exists():
                    try:
                        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
                    except Exception:
                        raw = {}
                raw.setdefault("adapters", {}).setdefault(aid, {})["default_model"] = model
                bak = backup_config()
                save_config(raw)
                return self._json({"ok": True, "id": aid, "default_model": model,
                                   "adapter": adapter_status(load_config()).get(aid, {}),
                                   "backup": bak.name})
            if p.startswith("/api/adapters/") and p.endswith("/rename"):
                aid = urllib.parse.unquote(p.split("/api/adapters/", 1)[1].rsplit("/", 1)[0])
                label = str(body.get("label") or "").strip()[:60]
                cfg = load_config()
                if aid not in (cfg.get("adapters") or {}):
                    return self._json({"error": f"未知适配器：{aid}"}, 404)
                if not label:
                    return self._json({"error": "名称不能为空"}, 400)
                raw = {}
                if CONFIG_PATH.exists():
                    try:
                        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
                    except Exception:
                        raw = {}
                raw.setdefault("adapters", {}).setdefault(aid, {})["label"] = label
                save_config(raw)
                return self._json({"ok": True, "id": aid, "label": label})
            if p == "/api/projects":
                orch_on = _orch_cfg(load_config())[0]
                ext_key = None
                if orch_on:
                    ext_key, ek_err = _parse_external_key(body)
                    if ek_err:
                        return self._json({"error": ek_err}, 400)
                name = str(body.get("name") or "").strip()[:80]
                if not name:
                    return self._json({"error": "项目名不能为空"}, 400)
                pid = "proj-" + uuid.uuid4().hex[:8]
                now = now_iso()
                conn = db()
                if ext_key:
                    hit = conn.execute("SELECT id FROM projects WHERE external_key=?", (ext_key,)).fetchone()
                    if hit:
                        d = project_public(conn, hit["id"])
                        conn.close()
                        return self._json({"ok": True, "project": d, "created": False})
                    same_dir = str(body.get("dir") or "").strip()
                    if same_dir and conn.execute(
                            "SELECT 1 FROM projects WHERE dir=? AND (external_key IS NULL OR external_key<>?)",
                            (same_dir, ext_key)).fetchone():
                        _orch_log(f"项目 dir 相同但 external_key 不同，不自动合并：dir={same_dir} key={ext_key}")
                try:
                    with conn:
                        conn.execute("INSERT INTO projects(id,name,dir,created_at,updated_at,external_key) VALUES(?,?,?,?,?,?)",
                                     (pid, name, str(body.get("dir") or "").strip(), now, now, ext_key))
                        for aid, ad in (load_config().get("adapters") or {}).items():
                            if (ad.get("type") or "").lower() == "human" and is_enabled(ad):
                                conn.execute("INSERT OR IGNORE INTO project_members(project_id,adapter_id,added_at) VALUES(?,?,?)",
                                             (pid, aid, now))
                except sqlite3.IntegrityError:
                    hit = conn.execute("SELECT id FROM projects WHERE external_key=?", (ext_key,)).fetchone()
                    if hit:
                        d = project_public(conn, hit["id"])
                        conn.close()
                        return self._json({"ok": True, "project": d, "created": False})
                    conn.close()
                    raise
                _dir_raw = str(body.get("dir") or "").strip()
                try:
                    _envmd_notice(conn, pid, resolve_path(_dir_raw) if _dir_raw else "")
                except Exception:  # noqa: BLE001
                    pass
                d = project_public(conn, pid)
                conn.close()
                result = {"ok": True, "project": d}
                if ext_key:
                    result["created"] = True
                return self._json(result)
            if p.startswith("/api/projects/") and p.endswith("/dir"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                d = str(body.get("dir") or "").strip()[:500]
                if not d:
                    return self._json({"error": "项目存储位置不能为空"}, 400)
                resolved = resolve_path(d)
                if not Path(resolved).is_dir():
                    return self._json({"error": f"目录不存在：{resolved}"}, 400)
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                with conn:
                    conn.execute("UPDATE projects SET dir=?, updated_at=? WHERE id=?", (resolved, now_iso(), pid))
                try:
                    _envmd_notice(conn, pid, resolved)
                except Exception:  # noqa: BLE001
                    pass
                project = project_public(conn, pid)
                conn.close()
                return self._json({"ok": True, "project": project})
            if p.startswith("/api/projects/") and p.endswith("/settings"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                v = body.get("inject_profile")
                with conn:
                    if v is not None:
                        conn.execute("UPDATE projects SET inject_profile=?, updated_at=? WHERE id=?",
                                     (1 if v else 0, now_iso(), pid))
                d = project_public(conn, pid)
                conn.close()
                return self._json({"ok": True, "project": d})
            if p.startswith("/api/projects/") and p.endswith("/notify-boss"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                reason = str(body.get("reason") or "需要拍板").strip()[:500]
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                conn.close()
                notify_boss(pid, reason)
                return self._json({"ok": True})
            if p.startswith("/api/projects/") and p.endswith("/roles"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                members = {x["adapter_id"] for x in conn.execute(
                    "SELECT adapter_id FROM project_members WHERE project_id=?", (pid,)).fetchall()}
                catalog = load_role_catalog()
                cfg_now = load_config()
                assignments = body.get("assignments") or {}
                wanted = set()
                if isinstance(assignments, dict):
                    for aids in assignments.values():
                        if isinstance(aids, list):
                            wanted |= {str(x) for x in aids if str(x).strip()}
                unknown = sorted(wanted - set((cfg_now.get("adapters") or {}).keys()))
                if unknown:
                    conn.close()
                    return self._json({"error": "岗位引用了不存在的 AI：" + "、".join(unknown)}, 400)
                # 选中未入群的 AI：保存岗位时一并加入项目群（整单事务）
                joined = sorted(wanted - members)
                effective_members = members | wanted
                errors, clean = validate_role_assignments(cfg_now, catalog, effective_members, assignments)
                if errors:
                    conn.close()
                    return self._json({"error": "岗位冲突：" + "；".join(errors), "errors": errors}, 400)
                now = now_iso()
                with conn:
                    for aid in joined:
                        conn.execute("INSERT OR IGNORE INTO project_members(project_id,adapter_id,added_at) VALUES(?,?,?)",
                                     (pid, aid, now))
                    conn.execute("DELETE FROM project_roles WHERE project_id=?", (pid,))
                    for rid, aids in clean.items():
                        for aid in aids:
                            conn.execute("INSERT OR IGNORE INTO project_roles(project_id,role_id,adapter_id,created_at) VALUES(?,?,?,?)",
                                         (pid, rid, aid, now))
                assigns = project_role_assignments(conn, pid)
                conn.close()
                return self._json({"ok": True, "assignments": assigns, "joined": joined})
            if p.startswith("/api/projects/") and p.endswith("/state"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                action = str(body.get("action") or "").strip().lower()
                target = {"pause": "paused", "resume": "running", "stop": "stopped"}.get(action)
                if not target:
                    return self._json({"error": "action 需为 pause/resume/stop"}, 400)
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                with conn:
                    conn.execute("UPDATE projects SET status=?, updated_at=? WHERE id=?", (target, now_iso(), pid))
                d = project_public(conn, pid)
                conn.close()
                return self._json({"ok": True, "project": d})
            if p.startswith("/api/projects/") and p.endswith("/messages"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                content = str(body.get("content") or "").strip()[:4000]
                if not content:
                    return self._json({"error": "消息不能为空"}, 400)
                conn = db()
                prow = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
                if not prow:
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                if (prow["status"] or "running") != "running":
                    conn.close()
                    return self._json({"error": "项目已暂停/停止，请先继续"}, 409)
                members = [x["adapter_id"] for x in conn.execute(
                    "SELECT adapter_id FROM project_members WHERE project_id=?", (pid,)).fetchall()]
                cfg = load_config()
                human_ids = {x for x in members if ((cfg.get("adapters", {}).get(x) or {}).get("type") or "").lower() == "human"}
                ai_members = [x for x in members if x not in human_ids]
                explicit_mentions = "mentions" in body
                raw_mentions = body.get("mentions")
                if raw_mentions is None:
                    raw_mentions = ["all"]
                if isinstance(raw_mentions, str):
                    raw_mentions = [raw_mentions]
                raw_lower = [str(x).lower() for x in raw_mentions]
                boss_mentioned = any(x in human_ids for x in [str(x) for x in raw_mentions])
                humans_hit = list(dict.fromkeys(str(x) for x in raw_mentions if str(x) in human_ids))
                if "all" in raw_lower:
                    targets = ai_members
                    mentions = ["all"] + humans_hit
                else:
                    targets = [str(x) for x in raw_mentions if str(x) in ai_members]
                    mentions = targets + [x for x in raw_mentions if str(x) in human_ids]
                if not targets and not boss_mentioned and not explicit_mentions:
                    conn.close()
                    return self._json({"error": "没有可用的 @AI；先把 AI 加入项目群"}, 400)
                now = now_iso()
                attachment_ids = body.get("attachment_ids") or []
                if not isinstance(attachment_ids, list):
                    attachment_ids = []
                attachments = attachment_meta(attachment_ids[:10])
                with conn:
                    conn.execute("INSERT INTO group_messages(project_id,role,content,adapter,mentions,attachments,created_at) VALUES(?,?,?,?,?,?,?)",
                                 (pid, "user", content, None, json.dumps(mentions, ensure_ascii=False),
                                  json.dumps(attachments, ensure_ascii=False) if attachments else None, now))
                    job_ids = []
                    for target in targets:
                        jid = uuid.uuid4().hex[:12]
                        conn.execute("INSERT INTO jobs(id,kind,target,model,status,error,ticket_id,project_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                     (jid, "group_chat", target, None, "running", None, None, pid, now, now))
                        job_ids.append(jid)
                conn.close()
                for jid, target in zip(job_ids, targets):
                    threading.Thread(target=run_group_job, args=(jid, pid, target, content, attachments), daemon=True).start()
                return self._json({"ok": True, "job_ids": job_ids, "targets": targets, "mentions": mentions,
                                   "attachments": attachments, "boss_notified": boss_mentioned and not targets})
            if p.startswith("/api/projects/") and p.endswith("/rename"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                name = str(body.get("name") or "").strip()[:80]
                if not name:
                    return self._json({"error": "项目名不能为空"}, 400)
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                with conn:
                    conn.execute("UPDATE projects SET name=?, updated_at=? WHERE id=?", (name, now_iso(), pid))
                d = project_public(conn, pid)
                conn.close()
                return self._json({"ok": True, "project": d})
            if p.startswith("/api/projects/") and p.endswith("/archive"):
                # 项目归档（CB-UI-PROJ-20261112）：archived=1 + paused 让派发器自然跳过；恢复=running
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                raw = body.get("archived", True)
                if isinstance(raw, str):
                    archived = raw.strip().lower() not in ("false", "0", "no", "off", "")
                else:
                    archived = bool(raw)
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                now = now_iso()
                with conn:
                    if archived:
                        conn.execute(
                            "UPDATE projects SET archived=1, archived_at=COALESCE(archived_at, ?), "
                            "status=CASE WHEN status IN ('paused','stopped') THEN status ELSE 'paused' END, "
                            "updated_at=? WHERE id=?", (now, now, pid))
                    else:
                        conn.execute(
                            "UPDATE projects SET archived=0, archived_at=NULL, status='running', "
                            "updated_at=? WHERE id=?", (now, pid))
                d = project_public(conn, pid)
                conn.close()
                return self._json({"ok": True, "project": d})
            if p.startswith("/api/projects/") and p.endswith("/members/batch"):
                # R3 原子保存：任一条冲突 → 400，整单不写
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].split("/", 1)[0])
                wanted_raw = body.get("member_ids") or body.get("members") or []
                if not isinstance(wanted_raw, list):
                    return self._json({"error": "member_ids 必须是数组"}, 400)
                cfg = load_config()
                catalog = load_role_catalog()
                wanted = {str(x).strip() for x in wanted_raw if str(x).strip()}
                unknown = [x for x in wanted if x not in (cfg.get("adapters") or {})]
                if unknown:
                    return self._json({"error": "未知适配器：" + "、".join(sorted(unknown))}, 400)
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                errors, plan = plan_member_sync(conn, cfg, catalog, pid, wanted)
                if errors:
                    conn.close()
                    return self._json({"error": "岗位冲突，整个保存已取消：" + "；".join(errors),
                                       "errors": errors}, 400)
                apply_member_sync(conn, pid, plan)
                d = project_public(conn, pid)
                conn.close()
                return self._json({"ok": True, "project": d,
                                   "inherited": plan["inherited"], "skipped": plan["skipped"]})
            if p.startswith("/api/projects/") and p.endswith("/members"):
                pid = urllib.parse.unquote(p.split("/api/projects/", 1)[1].rsplit("/", 1)[0])
                aid = str(body.get("adapter_id") or "").strip()
                action = str(body.get("action") or "add").strip().lower()
                cfg = load_config()
                if aid not in (cfg.get("adapters") or {}):
                    return self._json({"error": f"未知适配器：{aid}"}, 400)
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    conn.close()
                    return self._json({"error": "project not found"}, 404)
                current = {x["adapter_id"] for x in conn.execute(
                    "SELECT adapter_id FROM project_members WHERE project_id=?", (pid,)).fetchall()}
                wanted = set(current)
                if action == "remove":
                    wanted.discard(aid)
                else:
                    wanted.add(aid)
                errors, plan = plan_member_sync(conn, cfg, load_role_catalog(), pid, wanted)
                if errors:
                    conn.close()
                    return self._json({"error": "岗位冲突，整个保存已取消：" + "；".join(errors),
                                       "errors": errors}, 400)
                apply_member_sync(conn, pid, plan)
                d = project_public(conn, pid)
                conn.close()
                return self._json({"ok": True, "project": d,
                                   "inherited": plan["inherited"], "skipped": plan["skipped"]})
            if p == "/api/tickets":
                orch_on = _orch_cfg(load_config())[0]
                ext_key = None
                if orch_on:
                    ext_key, ek_err = _parse_external_key(body)
                    if ek_err:
                        return self._json({"error": ek_err}, 400)
                rid = body.get("id") or new_ticket_id(body.get("recipient") or "用户")
                title = str(body.get("title") or "未命名工单").strip()[:120]
                recipient = str(body.get("recipient") or "用户").strip()
                executor = str(body.get("executor") or "").strip()
                project_id = str(body.get("project_id") or "proj-default").strip()
                model = str(body.get("model") or "").strip() or None
                gate = str(body.get("gate") or "无").strip()
                prio = str(body.get("priority") or "P2").strip()
                note = str(body.get("note") or "").strip()
                if executor:
                    ad = find_adapter(load_config(), executor)
                    models = adapter_models(load_config(), ad)
                    if models:
                        if not model:
                            model = ad.get("default_model") or models[0]["id"]
                        if model not in {m["id"] for m in models}:
                            return self._json({"error": f"未知模型：{model}"}, 400)
                    else:
                        model = None
                else:
                    model = None
                conn = db()
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                    project_id = "proj-default"
                if ext_key:
                    hit = conn.execute("SELECT id, project_id FROM tickets WHERE external_key=?", (ext_key,)).fetchone()
                    if hit:
                        conn.close()
                        return self._json({"ok": True, "id": hit["id"],
                                           "project_id": hit["project_id"] or "proj-default", "created": False})
                try:
                    with conn:
                        conn.execute(
                            "INSERT INTO tickets(id,title,status,executor,model,recipient,gate,priority,resource,next_action,note,canonical_sha256,project_id,created_at,updated_at,external_key)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (rid, title, "待处理", executor, model, recipient, gate, prio, "repo:demo",
                             "等待派发", note, "manual", project_id, now_iso(), now_iso(), ext_key),
                        )
                        add_event(conn, rid, "intake_seen", "桌面端", "手工建单" + (f"，执行模型 {model}" if model else ""))
                        if executor:
                            add_event(conn, rid, "dispatch_intent", "桌面端", f"准备派给 {executor}")
                except sqlite3.IntegrityError:
                    hit = conn.execute("SELECT id, project_id FROM tickets WHERE external_key=?", (ext_key,)).fetchone()
                    if hit:
                        conn.close()
                        return self._json({"ok": True, "id": hit["id"],
                                           "project_id": hit["project_id"] or "proj-default", "created": False})
                    conn.close()
                    raise
                conn.close()
                result = {"ok": True, "id": rid, "project_id": project_id}
                if ext_key:
                    result["created"] = True
                return self._json(result)
            if p.startswith("/api/tickets/") and p.endswith("/model"):
                rid = urllib.parse.unquote(p.split("/api/tickets/", 1)[1].rsplit("/", 1)[0])
                model = str(body.get("model") or "").strip()
                conn = db()
                r = conn.execute("SELECT * FROM tickets WHERE id=?", (rid,)).fetchone()
                if not r:
                    conn.close()
                    return self._json({"error": "not found"}, 404)
                ad = find_adapter(load_config(), r["executor"] or "")
                models = adapter_models(load_config(), ad)
                if not models:
                    conn.close()
                    return self._json({"error": "该执行端没有可选模型"}, 400)
                if model not in {m["id"] for m in models}:
                    conn.close()
                    return self._json({"error": f"未知模型：{model}"}, 400)
                old = r["model"] or "auto"
                with conn:
                    conn.execute("UPDATE tickets SET model=?, updated_at=? WHERE id=?", (model, now_iso(), rid))
                    note = f"{old} → {model}"
                    if r["status"] == "派发中":
                        note += "（本轮已启动，下一轮派发/重试生效）"
                    else:
                        note += "（下一次派发生效）"
                    add_event(conn, rid, "model_switch", "主理人", note)
                conn.close()
                return self._json({"ok": True, "model": model})
            if p.startswith("/api/tickets/") and p.endswith("/advance"):
                cfg = load_config()
                if not (cfg.get("security") or {}).get("allow_demo", True):
                    return self._json({"error": "演示推进未开启"}, 403)
                rid = urllib.parse.unquote(p.split("/api/tickets/", 1)[1].rsplit("/", 1)[0])
                conn = db()
                r = conn.execute("SELECT * FROM tickets WHERE id=?", (rid,)).fetchone()
                if not r:
                    conn.close()
                    return self._json({"error": "not found"}, 404)
                cur = r["status"]
                nxt = NEXT_STATE.get(cur)
                if not nxt:
                    conn.close()
                    return self._json({"error": f"状态 {cur} 无演示推进路径"}, 400)
                with conn:
                    upd = conn.execute("UPDATE tickets SET status=?, updated_at=? WHERE id=? AND status=?",
                                       (nxt, now_iso(), rid, cur))
                    changed = upd.rowcount == 1
                    if changed:
                        add_event(conn, rid, "status", "演示", f"{cur} → {nxt}（演示推进；真实派发器接入后由台账更新）")
                        if nxt == "已验":
                            conn.execute("INSERT INTO signoffs(ticket_id,gate,signer,at,note) VALUES(?,?,?,?,?)",
                                         (rid, r["gate"], "主理人", now_iso(), "演示签收"))
                conn.close()
                if not changed:
                    return self._json({"error": "状态已被其它请求更新"}, 409)
                if nxt == "待验":
                    notify_boss(r["project_id"] or "proj-default",
                                f"{r['gate'] or '门禁'} 待拍板：{rid} {r['title']}", ticket_id=rid)
                return self._json({"ok": True, "status": nxt})
            if p.startswith("/api/tickets/") and p.endswith("/signoff"):
                rid = urllib.parse.unquote(p.split("/api/tickets/", 1)[1].rsplit("/", 1)[0])
                gate = str(body.get("gate") or "").strip()
                signer = str(body.get("signer") or "").strip()
                cfg = load_config()
                allowed_signers = set((cfg.get("security") or {}).get("allowed_signers") or ["主理人"])
                conn = db()
                r = conn.execute("SELECT * FROM tickets WHERE id=?", (rid,)).fetchone()
                if not r:
                    conn.close()
                    return self._json({"error": "工单不存在"}, 404)
                if r["status"] != "待验":
                    conn.close()
                    return self._json({"error": f"当前状态 {r['status']} 不能签收"}, 409)
                # C5：与前端 v2.2 对齐——无门禁记录（旧数据）不允许签收，需主理人先补录 gate
                if (r["gate"] or "").strip() in {"", "无", "-"}:
                    conn.close()
                    return self._json({"error": "本单没有门禁记录（可能是旧数据），不能签收；请联系主理人补录门禁后再试"}, 400)
                if not gate or gate != (r["gate"] or ""):
                    conn.close()
                    return self._json({"error": "gate 与工单不一致"}, 400)
                if signer not in allowed_signers:
                    conn.close()
                    return self._json({"error": "signer 不在允许名单"}, 403)
                with conn:
                    upd = conn.execute("UPDATE tickets SET status='已验', updated_at=? WHERE id=? AND status='待验'",
                                       (now_iso(), rid))
                    changed = upd.rowcount == 1
                    if changed:
                        conn.execute("INSERT INTO signoffs(ticket_id,gate,signer,at,note) VALUES(?,?,?,?,?)",
                                     (rid, gate, signer, now_iso(), str(body.get("note") or "")))
                        add_event(conn, rid, "gate_passed", signer, f"{gate} 签收")
                conn.close()
                if not changed:
                    return self._json({"error": "状态已被其它请求签收或更新"}, 409)
                return self._json({"ok": True})
            # ---- B1 驳回（WK v2 追加：仅当 status==待验 可驳回）----
            if p.startswith("/api/tickets/") and p.endswith("/reject"):
                rid = urllib.parse.unquote(p.split("/api/tickets/", 1)[1].rsplit("/", 1)[0])
                reason = str(body.get("reason") or "").strip()
                signer = str(body.get("signer") or "").strip() or "主理人"
                if not reason:
                    return self._json({"error": "驳回原因必填"}, 400)
                if len(reason) > 200:
                    return self._json({"error": "驳回原因不能超过 200 字"}, 400)
                conn = db()
                r = conn.execute("SELECT * FROM tickets WHERE id=?", (rid,)).fetchone()
                if not r:
                    conn.close()
                    return self._json({"error": "工单不存在"}, 404)
                if r["status"] != "待验":
                    conn.close()
                    return self._json({"error": f"当前状态 {r['status']} 不能驳回（仅待验可驳回）"}, 409)
                with conn:
                    upd = conn.execute(
                        "UPDATE tickets SET status='待处理', updated_at=? WHERE id=? AND status='待验'",
                        (now_iso(), rid))
                    changed = upd.rowcount == 1
                    if changed:
                        add_event(conn, rid, "rejected", signer, f"驳回：{reason}")
                conn.close()
                if not changed:
                    return self._json({"error": "状态已被其它请求更新"}, 409)
                return self._json({"ok": True, "status": "待处理"})
            # ---- B2 重新派发（WK v2 追加：仅当 status==失败 可重派）----
            if p.startswith("/api/tickets/") and p.endswith("/retry"):
                rid = urllib.parse.unquote(p.split("/api/tickets/", 1)[1].rsplit("/", 1)[0])
                conn = db()
                r = conn.execute("SELECT * FROM tickets WHERE id=?", (rid,)).fetchone()
                if not r:
                    conn.close()
                    return self._json({"error": "工单不存在"}, 404)
                if r["status"] != "失败":
                    conn.close()
                    return self._json({"error": f"当前状态 {r['status']} 不能重新派发（仅失败可重派）"}, 409)
                with conn:
                    upd = conn.execute(
                        "UPDATE tickets SET status='待处理', updated_at=? WHERE id=? AND status='失败'",
                        (now_iso(), rid))
                    changed = upd.rowcount == 1
                    if changed:
                        add_event(conn, rid, "retry_manual", "主理人", "手动重新派发")
                conn.close()
                if not changed:
                    return self._json({"error": "状态已被其它请求更新"}, 409)
                return self._json({"ok": True, "status": "待处理"})
            if p == "/api/models/refresh":
                ok, msg, models = refresh_workbuddy_models()
                return self._json({"ok": ok, "message": msg, "models": models}, 200 if ok else 502)
            if p == "/api/chat":
                target = str(body.get("target") or "demo").strip()
                prompt = str(body.get("message") or "").strip()
                ticket_id = body.get("ticket_id") or None
                model = str(body.get("model") or "").strip() or None
                attachment_ids = body.get("attachment_ids") or []
                if not isinstance(attachment_ids, list):
                    attachment_ids = []
                attachments = attachment_meta(attachment_ids[:10])
                if not prompt:
                    return self._json({"error": "消息为空"}, 400)
                cfg = load_config()
                ad = find_adapter(cfg, target)
                if not ad:
                    return self._json({"error": f"未知适配器：{target}"}, 400)
                if not is_enabled(ad):
                    return self._json({"error": f"适配器未启用：{target}"}, 400)
                models = adapter_models(cfg, ad)
                if models:
                    if not model:
                        model = ad.get("default_model") or models[0]["id"]
                    if model not in {m["id"] for m in models}:
                        return self._json({"error": f"未知模型：{model}"}, 400)
                else:
                    model = None
                job_id = uuid.uuid4().hex[:12]
                rounds = int((cfg.get("chat") or {}).get("context_rounds", 4))
                conn = db()
                hist = [dict(r) for r in conn.execute(
                    "SELECT role,content FROM chat_messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                    (target, max(1, rounds) * 2),
                ).fetchall()]
                context = list(reversed(hist))
                with conn:
                    conn.execute("INSERT INTO jobs(id,kind,target,model,status,error,ticket_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                 (job_id, "chat", target, model, "running", None, ticket_id, now_iso(), now_iso()))
                    conn.execute("INSERT INTO chat_messages(conversation_id,role,content,adapter,model,attachments,ticket_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                                 (target, "user", prompt, target, model, json.dumps(attachments, ensure_ascii=False) if attachments else None, ticket_id, now_iso()))
                conn.close()
                threading.Thread(target=run_chat_job, args=(job_id, target, prompt, ticket_id, model, context, attachments), daemon=True).start()
                return self._json({"ok": True, "job_id": job_id, "model": model, "attachments": attachments})
            if p == "/api/demo/tick":
                cfg = load_config()
                if not (cfg.get("security") or {}).get("allow_demo", True):
                    return self._json({"error": "演示 tick 未开启"}, 403)
                conn = db()
                with conn:
                    conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('last_tick',?)", (now_iso(),))
                    add_event(conn, "SYSTEM", "tick", "演示", "模拟一次派发器 tick")
                conn.close()
                return self._json({"ok": True, "tick": now_iso()})
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)

    def _sse(self, job_id: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last_id = 0
        deadline = time.time() + 240
        while time.time() < deadline:
            conn = db()
            rows = [dict(r) for r in conn.execute("SELECT * FROM job_events WHERE job_id=? AND id>? ORDER BY id", (job_id, last_id)).fetchall()]
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            conn.close()
            for r in rows:
                last_id = r["id"]
                payload = json.dumps({"type": r["type"], "content": r["content"], "at": r["at"]}, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                if r["type"] == "done":
                    return
            if job and job["status"] in ("done", "failed"):
                return
            time.sleep(0.4)
        try:
            payload = json.dumps({"type": "error", "content": "SSE 超时"}, ensure_ascii=False)
            self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass


def _load_external_token() -> str:
    """C8 外部自动化入口（可选、显式开启才生效）：
    ① 环境变量 AI_CHAT_EXTERNAL_TOKEN_FILE；② 配置 security.external_token_file。
    指向的文件内容（去首尾空白）作为第二个可用 X-Deck-Token，供外部脚本/其他 AI
    通过 HTTP 建项目/建单（配合 external_key 幂等）。不配置则行为零变化。
    注意：该文件长期有效，请放在仅本账户可读的位置。"""
    try:
        p = (os.environ.get("AI_CHAT_EXTERNAL_TOKEN_FILE") or "").strip()
        if not p:
            p = str((load_config().get("security") or {}).get("external_token_file") or "").strip()
        if not p:
            return ""
        f = Path(resolve_path(p))
        if not f.is_file():
            return ""
        return f.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return ""


_PATROL_STARTED = False


def _start_patrol() -> None:
    """巡检随服务启动：服务在跑才有巡检，服务关掉就彻底不跑。不注册任何计划任务。

    关闭方式：环境变量 AI_CHAT_PATROL=0 再启动。
    """
    global _PATROL_STARTED
    if _PATROL_STARTED:
        return
    if os.environ.get("AI_CHAT_PATROL", "1") == "0":
        return
    _PATROL_STARTED = True

    def _run():
        try:
            import 巡检

            巡检.loop()
        except Exception as exc:  # noqa: BLE001
            print(f"[巡检] 循环启动失败（不影响主服务）：{exc!r}", file=sys.stderr)

    threading.Thread(target=_run, daemon=True, name="patrol").start()


def start_server(port: int = 0):
    init_db()
    if 记忆:
        try:
            _mp = resolve_path((load_config().get("paths") or {}).get("project") or "")
            记忆.memory_ensure_index(load_config(), _mp)
            atexit.register(_记忆收尾)
        except Exception:  # noqa: BLE001
            pass
    token = secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.deck_token = token
    httpd.external_token = _load_external_token()
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    _start_patrol()
    return httpd, httpd.server_address[1], token


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-window", action="store_true")
    args = ap.parse_args()
    httpd, port, token = start_server(args.port)
    url = f"http://127.0.0.1:{port}/?token={urllib.parse.quote(token)}"
    print(f"{APP_TITLE} v{APP_VERSION} 已启动：http://127.0.0.1:{port}/")
    if args.no_window:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        try:
            import webview  # pywebview

            webview.create_window(APP_TITLE, url, width=1380, height=900, min_size=(1080, 680))
            webview.start()
        except Exception:
            edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            if Path(edge).exists():
                subprocess.Popen([edge, f"--app={url}", "--window-size=1380,900"])
                print("已用 Edge 应用窗口打开；关闭窗口后服务结束。")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass
            else:
                import webbrowser

                webbrowser.open(url)
                input("浏览器已打开，按 Enter 结束服务…")
    httpd.shutdown()
