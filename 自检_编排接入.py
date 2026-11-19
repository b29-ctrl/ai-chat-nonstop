#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI聊不停 · 外部编排接口 v1 自检（16 项，临时库 / 临时端口）。

零依赖：仅标准库 + 本项目 服务.py。
安全：DB_PATH / DATA / ORCH_LOG_PATH 全部指向临时目录；真实 台账.sqlite3
      只读计算 SHA256（用例 13 校验前后不变），不写、不启动派发器、不留常驻进程。
"""
import os
import sys
import json
import time
import sqlite3
import hashlib
import tempfile
import threading
import urllib.parse
import urllib.request
import urllib.error
import subprocess
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="orch_selftest_")
os.environ["ORCH_LOG_PATH"] = os.path.join(_TMP, "编排接入.log")
os.environ["MEM_LOG_PATH"] = os.path.join(_TMP, "记忆.log")
os.environ["PYTHONUTF8"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import 服务  # noqa: E402

REAL_LEDGER = os.path.join(HERE, "数据", "台账.sqlite3")

# ---- 全部指向临时库/临时目录（DATA 必须保持 Path，db() 会调用 DATA.mkdir） ----
服务.DATA = Path(_TMP) / "数据"
服务.DB_PATH = 服务.DATA / "台账.sqlite3"
服务.DATA.mkdir(parents=True, exist_ok=True)

PASS = 0
FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_db():
    conn = 服务.db()
    with conn:
        for t in ("tickets", "events", "signoffs", "group_messages", "chat_messages", "project_members"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM projects WHERE id<>'proj-default'")
    conn.close()


def qconn():
    c = sqlite3.connect(str(服务.DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def scalar(sql, args=()):
    c = qconn()
    v = c.execute(sql, args).fetchone()[0]
    c.close()
    return v


def rows(sql, args=()):
    c = qconn()
    r = [dict(x) for x in c.execute(sql, args).fetchall()]
    c.close()
    return r


def set_status(tid, status):
    c = 服务.db()
    with c:
        c.execute("UPDATE tickets SET status=?, updated_at=? WHERE id=?", (status, 服务.now_iso(), tid))
    c.close()


HTTPD = None
PORT = 0
TOKEN = ""


def req(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method)
    r.add_header("X-Deck-Token", TOKEN)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        return e.code, payload


def mk_ticket(key=None, **kw):
    body = {"title": kw.pop("title", "编排测试单"), **kw}
    if key:
        body["external_key"] = key
    return req("POST", "/api/tickets", body)


# ---------------- 用例 1：同 external_key 两次建单 ----------------
def t1():
    clean_db()
    s1, r1 = mk_ticket("wf:t1:abc")
    s2, r2 = mk_ticket("wf:t1:abc")
    n = scalar("SELECT COUNT(*) FROM tickets")
    ne = scalar("SELECT COUNT(*) FROM events WHERE kind IN ('intake_seen','dispatch_intent')")
    ok = (s1 == 200 and s2 == 200 and r1.get("created") is True
          and r2.get("created") is False and r1.get("id") == r2.get("id")
          and n == 1 and ne == 1)
    check("1 同key两次建单：同id/created true→false/1行/事件无新增", ok,
          f"s={s1,s2} created={r1.get('created'),r2.get('created')} n={n} ne={ne}")


# ---------------- 用例 2：并发两次同 key 建单 ----------------
def t2():
    clean_db()
    barrier = threading.Barrier(2)
    out = {}

    def w(k):
        barrier.wait()
        out[k] = mk_ticket("wf:t2:concurrent")

    ths = [threading.Thread(target=w, args=(i,)) for i in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=25)
    n = scalar("SELECT COUNT(*) FROM tickets")
    codes = sorted([out.get(0, (0, {}))[0], out.get(1, (0, {}))[0]])
    ok = n == 1 and all(c in (200, 409) for c in codes) and 500 not in codes
    check("2 并发两次同key：只1行、无500", ok, f"n={n} codes={codes} out={out}")


# ---------------- 用例 3：非法 external_key → 400 不落库 ----------------
def t3():
    clean_db()
    bad = ["中文键", "a" * 201, "has space", "wf:bad/slash", 123, ""]
    codes = []
    for b in bad:
        s, _ = req("POST", "/api/tickets", {"title": "非法", "external_key": b})
        codes.append(s)
    n = scalar("SELECT COUNT(*) FROM tickets")
    ok = all(c == 400 for c in codes) and n == 0
    check("3 非法external_key(中文/超长/空格/斜杠/数字/空串)→400不落库", ok, f"codes={codes} n={n}")


# ---------------- 用例 4：不带 external_key 行为与旧版一致 ----------------
def t4():
    clean_db()
    s, r = mk_ticket(None, executor="codex")
    evs = rows("SELECT kind FROM events ORDER BY id")
    kinds = [e["kind"] for e in evs]
    ok = (s == 200 and set(r.keys()) == {"ok", "id", "project_id"}
          and kinds == ["intake_seen", "dispatch_intent"])
    check("4 不带external_key：返回结构与旧版一致、事件2条", ok,
          f"keys={list(r.keys())} kinds={kinds}")


# ---------------- 用例 5：幂等建项目 ----------------
def t5():
    clean_db()
    s1, r1 = req("POST", "/api/projects", {"name": "编排项目", "external_key": "wf:proj:5"})
    s2, r2 = req("POST", "/api/projects", {"name": "重复项目", "external_key": "wf:proj:5"})
    n = scalar("SELECT COUNT(*) FROM projects WHERE id<>'proj-default'")
    ok = (s1 == 200 and s2 == 200 and r1.get("created") is True
          and r2.get("created") is False and r1["project"]["id"] == r2["project"]["id"] and n == 1)
    check("5 幂等建项目：两次同key同project/created true→false/1行", ok,
          f"created={r1.get('created'),r2.get('created')} n={n}")


# ---------------- 用例 6：pending counts 与 limit ----------------
def t6():
    clean_db()
    for key, st in (("wf:p:1", "待验"), ("wf:p:2", "失败"), ("wf:p:3", "待处理"),
                    ("wf:p:4", "待验"), ("wf:p:5", "已验")):
        _, r = mk_ticket(key)
        set_status(r["id"], st)
    _, allr = req("GET", "/api/orchestration/pending")
    _, lim = req("GET", "/api/orchestration/pending?limit=2")
    _, pid = req("GET", "/api/orchestration/pending?project_id=proj-default&limit=200")
    counts = allr.get("counts", {})
    ok = (counts == {"待验": 2, "失败": 1, "待处理": 1}
          and len(allr.get("items", [])) == 4
          and len(lim.get("items", [])) == 2
          and lim.get("counts") == counts
          and len(pid.get("items", [])) == 4
          and all(set(x) == {"id", "title", "status", "executor", "gate",
                             "project_id", "updated_at", "next_action"}
                  for x in allr.get("items", [])))
    check("6 pending：counts正确、limit生效、字段齐全", ok,
          f"counts={counts} all={len(allr.get('items',[]))} lim={len(lim.get('items',[]))}")


# ---------------- 用例 7：outbox 增量不丢不重、next 单调增、空库 ----------------
def t7():
    clean_db()
    _, empty = req("GET", "/api/orchestration/outbox")
    for k in ("wf:o:1", "wf:o:2", "wf:o:3"):
        mk_ticket(k)
    _, a = req("GET", "/api/orchestration/outbox?after=0&limit=2")
    _, b = req("GET", "/api/orchestration/outbox?after=%d&limit=2" % a.get("next", 0))
    seqs_a = [x["seq"] for x in a.get("items", [])]
    seqs_b = [x["seq"] for x in b.get("items", [])]
    ok = (empty.get("items") == [] and empty.get("next") == 0
          and len(seqs_a) == 2 and seqs_a == sorted(seqs_a)
          and a.get("next") == seqs_a[-1] and a["next"] > 0
          and all(s > a["next"] for s in seqs_b)
          and not (set(seqs_a) & set(seqs_b))
          and (not b.get("items") or b.get("next", 0) >= a["next"]))
    check("7 outbox：空库空items、增量不丢不重、next单调增", ok,
          f"empty={empty} a={seqs_a} b={seqs_b} na={a.get('next')} nb={b.get('next')}")


# ---------------- 用例 8：单工单回执含事件与签收；不存在 404 ----------------
def t8():
    clean_db()
    _, r = mk_ticket("wf:r:8")
    tid = r["id"]
    c = 服务.db()
    with c:
        c.execute("INSERT INTO signoffs(ticket_id,gate,signer,at,note) VALUES(?,?,?,?,?)",
                  (tid, "门①", "主理人", 服务.now_iso(), "测试签收"))
    c.close()
    s, d = req("GET", "/api/orchestration/tickets/" + urllib.parse.quote(tid))
    s404, _ = req("GET", "/api/orchestration/tickets/NO-SUCH-ID")
    ok = (s == 200 and d.get("ok") is True and d["ticket"]["id"] == tid
          and len(d.get("events", [])) >= 1 and len(d.get("signoffs", [])) == 1
          and s404 == 404)
    check("8 单工单回执：ticket+events+signoffs，不存在404", ok,
          f"ev={len(d.get('events',[]))} sign={len(d.get('signoffs',[]))} s404={s404}")


# ---------------- 用例 9：advance 并发两次 → 第二次 409、状态只进一档 ----------------
def t9():
    clean_db()
    _, r = mk_ticket("wf:adv:9")
    tid = r["id"]
    real_db = 服务.db
    sync = threading.Barrier(2)

    class SyncConn:
        """让两个并发请求都在 SELECT 到旧状态后再各自 UPDATE，精确复现竞态。"""
        def __init__(self, inner):
            self._c = inner

        def execute(self, sql, *a, **kw):
            if "SELECT * FROM tickets WHERE id=?" in sql:
                try:
                    sync.wait(timeout=8)
                except threading.BrokenBarrierError:
                    pass
            return self._c.execute(sql, *a, **kw)

        def __enter__(self):
            self._c.__enter__()
            return self

        def __exit__(self, *a):
            return self._c.__exit__(*a)

        def __getattr__(self, n):
            return getattr(self._c, n)

    服务.db = lambda: SyncConn(real_db())
    barrier = threading.Barrier(2)
    out = {}

    def w(k):
        barrier.wait()
        out[k] = req("POST", "/api/tickets/" + urllib.parse.quote(tid) + "/advance", {})

    ths = [threading.Thread(target=w, args=(i,)) for i in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=25)
    服务.db = real_db
    codes = sorted([out.get(0, (0, {}))[0], out.get(1, (0, {}))[0]])
    st = scalar("SELECT status FROM tickets WHERE id=?", (tid,))
    ev = scalar("SELECT COUNT(*) FROM events WHERE ticket_id=? AND kind='status'", (tid,))
    ok = codes == [200, 409] and st == "派发中" and ev == 1
    check("9 advance并发：第二次409、状态只进一档、事件1条", ok,
          f"codes={codes} st={st} ev={ev}")


# ---------------- 用例 10：signoff 门禁/白名单/状态 + 正确签收 ----------------
def t10():
    clean_db()
    _, r = mk_ticket("wf:sign:10", gate="门①")
    tid = r["id"]
    set_status(tid, "待验")
    s_badgate, _ = req("POST", "/api/tickets/" + tid + "/signoff", {"gate": "门②", "signer": "主理人"})
    s_badsigner, _ = req("POST", "/api/tickets/" + tid + "/signoff", {"gate": "门①", "signer": "路人"})
    _, r2 = mk_ticket("wf:sign:10b", gate="门①")
    s_notwait, _ = req("POST", "/api/tickets/" + r2["id"] + "/signoff", {"gate": "门①", "signer": "主理人"})
    s_ok, _ = req("POST", "/api/tickets/" + tid + "/signoff", {"gate": "门①", "signer": "主理人", "note": "通过"})
    st = scalar("SELECT status FROM tickets WHERE id=?", (tid,))
    nsign = scalar("SELECT COUNT(*) FROM signoffs WHERE ticket_id=?", (tid,))
    ok = (s_badgate == 400 and s_badsigner == 403 and s_notwait == 409
          and s_ok == 200 and st == "已验" and nsign == 1)
    check("10 signoff：错gate400/非白名单403/非待验409/正确→已验且1条签收", ok,
          f"{s_badgate,s_badsigner,s_notwait,s_ok} st={st} nsign={nsign}")


# ---------------- 用例 11：旧库迁移（无 external_key 列），启动两次不丢数据 ----------------
def t11():
    old = os.path.join(_TMP, "old_schema.db")
    c = sqlite3.connect(old)
    c.execute("""CREATE TABLE tickets(
        id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL, executor TEXT,
        model TEXT, recipient TEXT, gate TEXT, priority TEXT, resource TEXT, next_action TEXT,
        note TEXT, canonical_sha256 TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        project_id TEXT)""")
    c.execute("""CREATE TABLE projects(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, dir TEXT,
        status TEXT NOT NULL DEFAULT 'running', inject_profile INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    c.execute("INSERT INTO tickets(id,title,status,created_at,updated_at,project_id) VALUES('OLD-1','旧库工单','待处理','2026-01-01T00:00:00','2026-01-01T00:00:00','proj-default')")
    c.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES('old-proj','旧项目','2026-01-01T00:00:00','2026-01-01T00:00:00')")
    c.commit()
    c.close()
    saved = 服务.DB_PATH
    服务.DB_PATH = old
    err = ""
    try:
        服务.init_db()
        服务.init_db()
    except Exception as e:  # noqa: BLE001
        err = repr(e)
    finally:
        服务.DB_PATH = saved
    c = sqlite3.connect(old)
    tcols = {x[1] for x in c.execute("PRAGMA table_info(tickets)").fetchall()}
    pcols = {x[1] for x in c.execute("PRAGMA table_info(projects)").fetchall()}
    idx = {x[1] for x in c.execute("PRAGMA index_list(tickets)").fetchall()}
    n = c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    title = c.execute("SELECT title FROM tickets WHERE id='OLD-1'").fetchone()[0]
    pn = c.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    c.close()
    ok = (not err and "external_key" in tcols and "external_key" in pcols
          and "ux_tickets_external_key" in idx and n == 1 and title == "旧库工单" and pn >= 1)
    check("11 旧库迁移：启动两次无异常、列/唯一索引齐、数据不丢", ok,
          f"err={err} tcols={('external_key' in tcols)} pcols={('external_key' in pcols)} idx={idx} n={n}")


# ---------------- 用例 12：双模式（不传key/不调端点等价；enabled=false 仍可探测） ----------------
def t12():
    clean_db()
    s_health, h = req("GET", "/api/health")
    s_state, st = req("GET", "/api/state")
    real_cfg = 服务.load_config()
    real_load = 服务.load_config
    服务.load_config = lambda: {**real_cfg, "orchestration": {"enabled": False, "outbox_limit": 100}}
    try:
        s_pend, pend = req("GET", "/api/orchestration/pending")
        s_out, out = req("GET", "/api/orchestration/outbox")
        s_t, rr = mk_ticket("wf:off:12")
    finally:
        服务.load_config = real_load
    ok = (s_health == 200 and h.get("ok") is True and s_state == 200 and "tickets" in st
          and s_pend == 200 and pend.get("ok") is True
          and s_out == 200 and out.get("ok") is True
          and s_t == 200 and "created" not in rr)
    check("12 双模式：旧端点正常、enabled=false 新端点可探测且不404/403、带key走旧逻辑", ok,
          f"health={s_health} state={s_state} pend={s_pend} out={s_out} t={s_t} keys={list(rr.keys())}")


# ---------------- 用例 13：notify_boss 60s 去重 ----------------
def t13():
    clean_db()
    _, r = mk_ticket("wf:nb:13", gate="门①")
    tid = r["id"]
    服务._NOTIFY_DEDUP.clear()
    服务.notify_boss("proj-default", "同一原因请拍板", ticket_id=tid)
    服务.notify_boss("proj-default", "同一原因请拍板", ticket_id=tid)
    服务.notify_boss("proj-default", "另一原因请拍板", ticket_id=tid)
    n = scalar("SELECT COUNT(*) FROM group_messages WHERE role='alert'")
    ok = n == 2
    check("13 notify_boss：同键60s内只发1次、不同原因仍可发", ok, f"n={n}")


# ---------------- 用例 14：outbox 关联字段与孤儿 ticket_id 置空 ----------------
def t14():
    clean_db()
    _, r = mk_ticket("wf:join:14", gate="门①")
    tid = r["id"]
    c = 服务.db()
    with c:
        c.execute("INSERT INTO events(ticket_id,at,kind,actor,detail) VALUES(?,?,?,?,?)",
                  ("NO-SUCH-TICKET", 服务.now_iso(), "orphan", "测试", "孤儿事件"))
    c.close()
    _, d = req("GET", "/api/orchestration/outbox?after=0")
    items = d.get("items", [])
    good = next((x for x in items if x["ticket_id"] == tid), None)
    orph = next((x for x in items if x["ticket_id"] == "NO-SUCH-TICKET"), None)
    ok = (good is not None and good["gate"] == "门①" and good["title"] == "编排测试单"
          and orph is not None and orph["status"] == "" and orph["title"] == ""
          and all(set(x) == {"seq", "at", "kind", "actor", "detail", "ticket_id",
                             "status", "gate", "project_id", "title"} for x in items))
    check("14 outbox：LEFT JOIN 带出status/gate/title、孤儿工单字段置空", ok,
          f"good={good} orph={orph}")


# ---------------- 用例 15：回归（旧自检 + py_compile） ----------------
def t15():
    env = {**os.environ, "PYTHONUTF8": "1"}
    results = {}
    for name, cmd in (
        ("记忆自检", [sys.executable, os.path.join(HERE, "自检_记忆.py")]),
        ("派发器自检", [sys.executable, os.path.join(HERE, "自检_派发器.py")]),
    ):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               cwd=HERE, env=env, encoding="utf-8", errors="replace")
            out = (p.stdout or "") + (p.stderr or "")
            results[name] = (p.returncode == 0, out)
        except Exception as e:  # noqa: BLE001
            results[name] = (False, repr(e))
    py = subprocess.run([sys.executable, "-m", "py_compile",
                         os.path.join(HERE, "服务.py"), os.path.join(HERE, "记忆.py"),
                         os.path.join(HERE, "自检_记忆.py"), os.path.join(HERE, "自检_编排接入.py")],
                        capture_output=True, text=True, timeout=120, cwd=HERE, env=env,
                        encoding="utf-8", errors="replace")
    m_ok, m_out = results["记忆自检"]
    d_ok, d_out = results["派发器自检"]
    m18 = "PASS=18" in m_out and "FAIL=0" in m_out
    d19 = "19/19" in d_out
    ok = m_ok and d_ok and m18 and d19 and py.returncode == 0
    check("15 回归：自检_记忆 PASS=18 / 派发器 19/19 / py_compile 4文件 OK", ok,
          f"mem={m_ok,m18} disp={d_ok,d19} py={py.returncode} {py.stderr[:120]}")


# ---------------- 用例 16：真实台账 SHA256 不变 + 无残留 ----------------
def t16():
    before = sha256(REAL_LEDGER) if os.path.exists(REAL_LEDGER) else "NO_LEDGER"
    after = sha256(REAL_LEDGER) if os.path.exists(REAL_LEDGER) else "NO_LEDGER"
    no_watch = not hasattr(__import__("记忆"), "_WATCHES")
    tmp_log = os.path.exists(os.environ["ORCH_LOG_PATH"])  # 日志落在临时目录
    ok = before == after and no_watch
    check("16 真实台账SHA256前后一致、无常驻watch进程表", ok,
          f"before={before[:12]} after={after[:12]} no_watch={no_watch} tmp_log={tmp_log}")
    return before


if __name__ == "__main__":
    print("=== AI聊不停 · 外部编排接口 v1 自检（16 项，临时库/临时端口） ===")
    SHA_BEFORE = sha256(REAL_LEDGER) if os.path.exists(REAL_LEDGER) else "NO_LEDGER"
    服务.init_db()
    # 隔离：真实配置可能 memory.enabled=true；自检期间不触发真实 memsearch 索引
    _real_index = getattr(服务.记忆, "memory_ensure_index", None) if 服务.记忆 else None
    if 服务.记忆 and _real_index:
        服务.记忆.memory_ensure_index = lambda *a, **k: None
    HTTPD, PORT, TOKEN = 服务.start_server(0)
    if 服务.记忆 and _real_index:
        服务.记忆.memory_ensure_index = _real_index
    print(f"  临时库={服务.DB_PATH}\n  临时端口={PORT}")
    try:
        clean_db()
        for fn in (t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, t12, t13, t14, t15):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                FAIL += 1
                print(f"  FAIL  {fn.__name__} 未捕获异常：{e!r}")
        t16()
        SHA_AFTER = sha256(REAL_LEDGER) if os.path.exists(REAL_LEDGER) else "NO_LEDGER"
        if SHA_BEFORE != SHA_AFTER:
            FAIL += 1
            print("  FAIL  全局：真实台账 SHA256 自检前后不一致")
        else:
            print(f"  PASS  全局：真实台账 SHA256 前后一致 {SHA_BEFORE[:16]}…")
    finally:
        HTTPD.shutdown()
        HTTPD.server_close()
        服务.记忆 and 服务.记忆.memory_close_all()
        print("  临时服务已 shutdown/server_close，无常驻进程")
    print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
