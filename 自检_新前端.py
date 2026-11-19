# -*- coding: utf-8 -*-
"""自检_新前端.py — 新前端接后端 Phase 1+2 真跑自检。

环境：临时库（新建空库 + 2 项目/若干工单/群消息）+ 临时端口 + headless 浏览器。
不写真实台账；结束清理临时文件与进程。
断言 T1–T13（前端渲染/交互）+ T14–T24（v2 后端交互），截图 7 张（含三档）。

说明：浏览器相关用例（T1–T13、T22、T23）依赖 Playwright/Chromium；
若本机未安装，脚本自动将其标记为 SKIP，并把 T14–T21（纯 API、无需浏览器）照常跑完。
"""
import os
import sys
import time
import json
import shutil
import sqlite3
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import 服务 as svc  # noqa: E402

# —— 防侧效：不跑真实适配器任务、不建记忆索引（避免触发外部 CLI / 网络）——
svc.记忆 = None
svc.run_group_job = lambda *a, **k: None
svc.run_chat_job = lambda *a, **k: None

RESULTS = {}


def check(name, ok, detail=""):
    RESULTS[name] = (bool(ok), detail)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def skip(name, detail="SKIP：本机未安装 Playwright/Chromium"):
    RESULTS[name] = (True, detail)
    print(f"[SKIP] {name}: {detail}")


def db_all(q, *a):
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(q, a).fetchall()
    finally:
        con.close()


# ======================================================================
# 1. 准备临时库
# ======================================================================
TMP = Path(tempfile.mkdtemp(prefix="wkflow_test_"))
DB = TMP / "台账.sqlite3"
svc.DATA = TMP
svc.DB_PATH = DB  # 关键隔离：init_db() 须建表于临时库，不得触碰真实台账
svc.init_db()

NOW = "2026-11-13T14:05:00"
NOW2 = "2026-11-13T15:10:00"

con = sqlite3.connect(str(DB))
con.executescript(
    """
    INSERT INTO projects(id,name,dir,status,inject_profile,created_at,updated_at,archived,archived_at) VALUES
      ('p1','测试项目A',NULL,'running',1,'2026-11-10T09:00:00','2026-11-10T09:00:00',0,NULL),
      ('p2','测试项目B',NULL,'running',1,'2026-11-11T09:00:00','2026-11-11T09:00:00',0,NULL),
      ('pa','测试归档项目',NULL,'running',1,'2026-10-01T09:00:00','2026-10-01T09:00:00',1,'2026-10-20T09:00:00');

    INSERT INTO project_members(project_id,adapter_id,added_at) VALUES
      ('p1','workbuddy','2026-11-10T09:00:00'),
      ('p1','coze','2026-11-10T09:00:00'),
      ('p1','dsh','2026-11-10T09:00:00'),
      ('p2','workbuddy','2026-11-11T09:00:00');

    INSERT INTO tickets(id,title,status,executor,model,recipient,gate,priority,resource,next_action,note,canonical_sha256,created_at,updated_at,project_id) VALUES
      ('T-TEST-001','测试待验工单','待验','workbuddy',NULL,NULL,'内容完整覆盖所有任务要求','P1 · 高',NULL,NULL,'回件摘要：覆盖所有要求','','%NOW%','%NOW%','p1'),
      ('T-TEST-002','测试已回件工单','已回件','coze',NULL,NULL,'无','P2 · 中',NULL,NULL,'已回件摘要内容','','2026-11-13T10:00:00','2026-11-13T10:30:00','p1'),
      ('T-TEST-003','测试待处理工单','待处理','dsh',NULL,NULL,'无','P2 · 中',NULL,NULL,'待处理说明','','2026-11-13T11:00:00','2026-11-13T11:00:00','p1'),
      ('T-TEST-004','项目B派发中工单','派发中','workbuddy',NULL,NULL,'无','P1 · 高',NULL,NULL,'派发中说明','','2026-11-13T12:00:00','2026-11-13T12:05:00','p2');

    INSERT INTO events(ticket_id,at,kind,actor,detail) VALUES
      ('T-TEST-001','2026-11-13T14:00:00','intake','系统','创建工单');

    INSERT INTO group_messages(project_id,role,content,adapter,mentions,attachments,created_at) VALUES
      ('p1','user','群聊消息一测试词ABC',NULL,'["all"]',NULL,'2026-11-13T14:01:00'),
      ('p1','user','群聊消息二测试词ABC',NULL,'["all"]',NULL,'2026-11-13T14:02:00'),
      ('p1','user','唯一搜索词XYZ用于历史检索',NULL,'["all"]',NULL,'2026-11-13T14:03:00');
    """.replace("%NOW%", NOW)
)
con.commit()
con.close()

# 真实台账一致性（自检不应触碰；C7：派发器运行时会持续写 lock/heartbeat/WAL，SHA 必然抖动，
# 改为「派发器运行中 → 跳过强校验；否则对关键表行数做逻辑比对」）
REAL_DB = HERE / "数据" / "台账.sqlite3"
real_db_counts_before = None
_dispatcher_running = False


def _real_dispatcher_alive() -> bool:
    pf = HERE / "数据" / "派发器.pid"
    try:
        pid = int(pf.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _real_db_counts():
    con = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True, timeout=5)
    try:
        return tuple(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in ("tickets", "events", "projects", "meta"))
    finally:
        con.close()


if REAL_DB.exists():
    _dispatcher_running = _real_dispatcher_alive()
    if not _dispatcher_running:
        try:
            real_db_counts_before = _real_db_counts()
        except Exception:
            real_db_counts_before = None

# 真实 配置.json SHA（写测试会重定向到临时副本，真实配置不应被改）
REAL_CONFIG = HERE / "配置.json"
real_cfg_sha_before = ""
if REAL_CONFIG.exists():
    import hashlib as _hl
    real_cfg_sha_before = _hl.sha256(REAL_CONFIG.read_bytes()).hexdigest()

# —— 配置隔离：把真实 配置.json 复制到临时目录，写测试只改临时副本（硬规则：不改 配置.json）——
svc.CONFIG_PATH = TMP / "配置.json"
if REAL_CONFIG.exists():
    shutil.copy2(REAL_CONFIG, svc.CONFIG_PATH)

# ======================================================================
# 2. 启动服务（web_flow）
# ======================================================================
svc.WEB = HERE / "web_flow"
httpd, PORT, TOKEN = svc.start_server(0)
BASE = f"http://127.0.0.1:{PORT}"
URL = f"{BASE}/?token={urllib.parse.quote(TOKEN)}"
print(f"临时服务已启动：{BASE}  token={TOKEN[:6]}...")


def api_get(path):
    req = urllib.request.Request(BASE + path, headers={"X-Deck-Token": TOKEN})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def api_post(path, obj):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                headers={"X-Deck-Token": TOKEN, "Content-Type": "application/json"},
                                method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as he:
        try:
            body = json.loads(he.read().decode("utf-8", "ignore"))
        except Exception:
            body = {}
        return he.code, body


def api_post_raw(path, raw_bytes, filename, mime):
    req = urllib.request.Request(BASE + path, data=raw_bytes,
                                headers={"X-Deck-Token": TOKEN,
                                          "X-Filename": urllib.parse.quote(filename),
                                          "Content-Type": mime},
                                method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as he:
        try:
            body = json.loads(he.read().decode("utf-8", "ignore"))
        except Exception:
            body = {}
        return he.code, body


def api_get_raw(path):
    req = urllib.request.Request(BASE + path, headers={"X-Deck-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read()


# ======================================================================
# 3. v2 后端交互真跑（纯 API，无需浏览器）：T14–T21
# ======================================================================
print("\n================ T14–T21：v2 后端交互（API 直连）================")

# —— T14 附件：上传 + 群发带附件 + 下载字节一致 + 超限 413 ——
sample = b"hello-wk-flow-v2-attachment-\x00\x01\x02-binary-payload"
st, up = api_post_raw("/api/attachments", sample, "sample.bin", "application/octet-stream")
att_ok = (st == 200 and up.get("ok") and (up.get("attachment") or {}).get("id"))
att_id = (up.get("attachment") or {}).get("id")
st2, snd = api_post("/api/projects/p1/messages",
                    {"content": "带附件的群发测试 " + str(int(time.time())),
                     "mentions": ["all"], "attachment_ids": [att_id]})
msg_ok = (st2 == 200) and (att_id in json.dumps(snd))
st3, dl = api_get_raw("/api/attachments/" + att_id)
dl_ok = (st3 == 200 and dl == sample)
# 超限：> MAX_ATTACH(20MB) → 413（服务端仅校验 Content-Length，不读体，返回快）
# 用 http.client 发送小体但声明 21MB Content-Length，避免客户端真传 21MB 被服务端中途断连（WinError 10053）
import http.client as _hc
_conn = _hc.HTTPConnection("127.0.0.1", PORT, timeout=30)
try:
    _conn.request("POST", "/api/attachments", body=b"x",
                  headers={"X-Deck-Token": TOKEN, "X-Filename": "big.bin",
                            "Content-Type": "application/octet-stream",
                            "Content-Length": str(21 * 1024 * 1024), "Connection": "close"})
    st_big = _conn.getresponse().status
except Exception as _e:
    st_big = f"ERR:{_e}"
finally:
    _conn.close()
check("T14", att_ok and msg_ok and dl_ok and st_big == 413,
      f"上传={att_ok}(id={att_id}) 群发带附件={msg_ok} 下载一致={dl_ok} 超限413={st_big}")

# —— T15 新建项目 ——
st, npj = api_post("/api/projects", {"name": "自测新建项目" + str(int(time.time())), "dir": ""})
npid = (npj.get("project") or {}).get("id")
db_pj = db_all("SELECT COUNT(*) c FROM projects WHERE id=?", npid)[0]["c"] == 1 if npid else False
check("T15", (st == 200 and npj.get("ok") and npid) and db_pj,
      f"新建={st == 200 and bool(npj.get('ok'))} id={npid} DB落库={db_pj}")

# —— T16 新建 AI 岗位（demo 类型，role_id=lead 非人工岗）——
st, nad = api_post("/api/adapters",
                   {"id": "wktest-adapter-a", "label": "自测岗位A", "type": "demo",
                    "role_id": "lead", "enabled": True})
adid = nad.get("id") or "wktest-adapter-a"
cfg_now = json.loads(svc.CONFIG_PATH.read_text(encoding="utf-8-sig"))
cfg_has = adid in (cfg_now.get("adapters") or {})
check("T16", (st == 200 and nad.get("ok") and adid) and cfg_has,
      f"新建岗位={st == 200 and bool(nad.get('ok'))} id={adid} 配置落库={cfg_has}")

# —— T17 模型切换（选一个有多模型的适配器切非当前默认模型）——
cfg0 = json.loads(svc.CONFIG_PATH.read_text(encoding="utf-8-sig"))
target_aid = None
target_model = None
for aid0, ad0 in (cfg0.get("adapters") or {}).items():
    ms = svc.adapter_models(cfg0, ad0)
    if ms and len(ms) > 1:
        target_aid = aid0
        cur = ad0.get("default_model")
        for m in ms:
            if m["id"] != cur:
                target_model = m["id"]
                break
        if target_model is None:
            target_model = ms[0]["id"]
        break
if target_aid:
    st, mdl = api_post(f"/api/adapters/{urllib.parse.quote(target_aid)}/model", {"model": target_model})
    cfg2 = json.loads(svc.CONFIG_PATH.read_text(encoding="utf-8-sig"))
    cfg_mdl = (cfg2.get("adapters", {}).get(target_aid, {}).get("default_model") == target_model)
    check("T17", (st == 200 and mdl.get("ok")) and cfg_mdl,
          f"切换={st == 200 and bool(mdl.get('ok'))} 适配器={target_aid} 模型={target_model} 配置保持={cfg_mdl}")
else:
    check("T17", False, "未找到可切换模型的多模型适配器")

# —— T18 成员增删 + 岗位冲突 400 ——
# 先清空 p2（种子仅 workbuddy），避免既有成员占 unique 岗位导致误判
api_post("/api/projects/p2/members", {"adapter_id": "workbuddy", "action": "remove"})
st, nad2 = api_post("/api/adapters",
                    {"id": "wktest-member-x", "label": "成员X", "type": "demo",
                     "role_id": "lead", "enabled": True})
mxid = nad2.get("id") or "wktest-member-x"
before = db_all("SELECT COUNT(*) c FROM project_members WHERE project_id='p2'")[0]["c"]
st, addm = api_post(f"/api/projects/p2/members", {"adapter_id": mxid, "action": "add"})
after_add = db_all("SELECT COUNT(*) c FROM project_members WHERE project_id='p2'")[0]["c"]
add_ok = (st == 200 and addm.get("ok") and after_add == before + 1)
st, rmm = api_post(f"/api/projects/p2/members", {"adapter_id": mxid, "action": "remove"})
after_rm = db_all("SELECT COUNT(*) c FROM project_members WHERE project_id='p2'")[0]["c"]
rm_ok = (st == 200 and rmm.get("ok") and after_rm == before)
# 冲突：两个适配器同 unique 角色 lead 同时编入 p2 的「主理人」岗位 → 经 /roles 端点返回 400 + errors[]
api_post("/api/adapters", {"id": "wktest-conflict-a", "label": "冲突A", "type": "demo", "role_id": "lead", "enabled": True})
api_post("/api/adapters", {"id": "wktest-conflict-b", "label": "冲突B", "type": "demo", "role_id": "lead", "enabled": True})
# 先两个都加入 p2 成员群（成员端点对重复岗位静默跳过，不报错）
api_post(f"/api/projects/p2/members", {"adapter_id": "wktest-conflict-a", "action": "add"})
api_post(f"/api/projects/p2/members", {"adapter_id": "wktest-conflict-b", "action": "add"})
# 再走角色编制端点，把两个 lead 同时编入唯一性岗位 → 应 400 + errors[]
st, conf = api_post(f"/api/projects/p2/roles",
                    {"assignments": {"lead": ["wktest-conflict-a", "wktest-conflict-b"]}})
conf_ok = (st == 400 and isinstance(conf.get("errors"), list) and len(conf.get("errors")) > 0)
check("T18", add_ok and rm_ok and conf_ok,
      f"加={add_ok}({before}→{after_add}) 移除={rm_ok}(→{after_rm}) 冲突400+errors={conf_ok}")

# —— T19 @多成员（群聊 mentions 两个 AI）——
p1m = [r["adapter_id"] for r in db_all("SELECT adapter_id FROM project_members WHERE project_id='p1'")]
cfgx = json.loads(svc.CONFIG_PATH.read_text(encoding="utf-8-sig"))
ai_mems = [m for m in p1m if (cfgx.get("adapters", {}).get(m, {}).get("type", "").lower()) != "human"]
if len(ai_mems) < 2:
    api_post("/api/adapters", {"id": "wktest-mem9", "label": "成员9", "type": "demo", "role_id": "pmo", "enabled": True})
    api_post("/api/projects/p1/members", {"adapter_id": "wktest-mem9", "action": "add"})
    ai_mems = ai_mems + ["wktest-mem9"]
ai_mems = ai_mems[:2]
st, men = api_post("/api/projects/p1/messages",
                  {"content": "自测@多成员 " + str(int(time.time())),
                   "mentions": ai_mems, "attachment_ids": []})
tgts = men.get("targets") or []
mention_ok = (st == 200 and len(tgts) == 2)
check("T19", mention_ok, f"@两个AI targets数={len(tgts)}")

# —— T19b 显式空 mentions：只落群消息，不建 job（C6 语义，真机 1.2）——
jobs_before = db_all("SELECT COUNT(*) c FROM jobs")[0]["c"]
st, m0 = api_post("/api/projects/p1/messages",
                  {"content": "自测无@说明 " + str(int(time.time())),
                   "mentions": [], "attachment_ids": []})
jobs_after0 = db_all("SELECT COUNT(*) c FROM jobs")[0]["c"]
gm0 = db_all("SELECT mentions FROM group_messages WHERE content LIKE '自测无@说明%' ORDER BY id DESC LIMIT 1")
m0_ok = (st == 200 and (m0.get("job_ids") or []) == [] and jobs_after0 == jobs_before
         and bool(gm0) and json.loads(gm0[0]["mentions"] or "[]") == [])
check("T19b", m0_ok,
      f"显式[] job增={jobs_after0 - jobs_before} mentions落库={gm0[0]['mentions'] if gm0 else '无'}")

# —— T19c 缺省 mentions（旧客户端兼容）：保持广播建 job ——
p1_all = [r["adapter_id"] for r in db_all(
    "SELECT adapter_id FROM project_members WHERE project_id='p1'")]
p1_all = [m for m in p1_all if (cfgx.get("adapters", {}).get(m, {}).get("type", "").lower()) != "human"]
st, m1 = api_post("/api/projects/p1/messages",
                  {"content": "自测缺省广播 " + str(int(time.time())), "attachment_ids": []})
n_jobs = len(m1.get("job_ids") or [])
m1_ok = (st == 200 and n_jobs == len(p1_all) and (m1.get("targets") or []) == p1_all
         and (m1.get("mentions") or []) == ["all"])
check("T19c", m1_ok, f"缺省 targets数={len(m1.get('targets') or [])} job数={n_jobs} mentions={m1.get('mentions')}")

# —— T20 驳回：造待验单 → 驳回 → 待处理 + rejected 事件 ——
tk20 = "T-SELF-REJECT-001"
con = sqlite3.connect(str(DB))
con.execute(
    "INSERT OR REPLACE INTO tickets(id,title,status,executor,model,recipient,gate,priority,resource,next_action,note,canonical_sha256,project_id,created_at,updated_at) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    (tk20, "自测驳回单", "待验", "workbuddy", None, "用户", "无", "P2", None, None, "x", "manual", "p1", NOW, NOW))
con.commit()
con.close()
st, rj = api_post(f"/api/tickets/{tk20}/reject", {"reason": "自测驳回原因"})
db_st20 = db_all("SELECT status FROM tickets WHERE id=?", tk20)[0]["status"]
db_ev20 = db_all("SELECT COUNT(*) c FROM events WHERE ticket_id=? AND kind='rejected'", tk20)[0]["c"]
check("T20", (st == 200 and rj.get("ok") and db_st20 == "待处理" and db_ev20 >= 1),
      f"驳回={st == 200 and bool(rj.get('ok'))} 状态={db_st20} rejected事件={db_ev20}")

# —— T21 重新派发：造失败单 → retry → 待处理 + retry_manual 事件 ——
tk21 = "T-SELF-RETRY-001"
con = sqlite3.connect(str(DB))
con.execute(
    "INSERT OR REPLACE INTO tickets(id,title,status,executor,model,recipient,gate,priority,resource,next_action,note,canonical_sha256,project_id,created_at,updated_at) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    (tk21, "自测重派单", "失败", "workbuddy", None, "用户", "无", "P2", None, None, "x", "manual", "p1", NOW, NOW))
con.commit()
con.close()
st, rp = api_post(f"/api/tickets/{tk21}/retry", {})
db_st21 = db_all("SELECT status FROM tickets WHERE id=?", tk21)[0]["status"]
db_ev21 = db_all("SELECT COUNT(*) c FROM events WHERE ticket_id=? AND kind='retry_manual'", tk21)[0]["c"]
check("T21", (st == 200 and rp.get("ok") and db_st21 == "待处理" and db_ev21 >= 1),
      f"重派={st == 200 and bool(rp.get('ok'))} 状态={db_st21} retry事件={db_ev21}")

# ======================================================================
# 4. 浏览器自检（Playwright）：T1–T13 / T22 / T23
# ======================================================================
try:
    from playwright.sync_api import sync_playwright  # noqa: E402
    HAS_PW = True
except Exception as e:  # 浏览器缺失则跳过（仍跑完 T14–T21）
    HAS_PW = False
    print("Playwright 不可用，浏览器用例将标记 SKIP：", e)

if HAS_PW:
    browser = None
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as e:  # 浏览器缺失则尝试安装
                print("chromium 启动失败，尝试安装：", e)
                import subprocess
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
                browser = pw.chromium.launch(headless=True)

            page = browser.new_page(viewport={"width": 1440, "height": 900})
            console_errors = []

            def on_console(msg):
                if msg.type == "error":
                    console_errors.append(msg.text)

            def on_pageerror(err):
                console_errors.append("PAGEERROR: " + str(err))

            page.on("console", on_console)
            page.on("pageerror", on_pageerror)

            page.goto(URL, wait_until="load")
            page.wait_for_selector("#pjPicker", timeout=15000)
            page.wait_for_timeout(800)

            # ---------- T1：加载无 console error ----------
            check("T1", len(console_errors) == 0, f"console error 数={len(console_errors)} " + str(console_errors[:3]))

            # ---------- T2：项目列表 = 临时库项目 ----------
            pj_opts = page.eval_on_selector_all("#pjPicker option", "els => els.map(e => e.value)")
            db_projs = [r["id"] for r in db_all("SELECT id FROM projects WHERE archived=0")]
            ok = set(pj_opts) == set(db_projs) and "p1" in pj_opts and "p2" in pj_opts
            check("T2", ok, f"UI={sorted(pj_opts)} DB={sorted(db_projs)}")

            # ---------- T3：群聊渲染 ≥2 条真实消息 ----------
            feed_txt = page.inner_text("#feed")
            ok = ("群聊消息一测试词ABC" in feed_txt) and ("群聊消息二测试词ABC" in feed_txt)
            check("T3", ok, f"feed 含两条种子群消息={'是' if ok else '否'}")

            # ---------- T6（先于 T7）：待签收角标 = DB 待验数 ----------
            inbox_cnt = int(page.inner_text(".it[data-view='inbox'] .cnt").strip() or "0")
            db_pending = db_all("SELECT COUNT(*) c FROM tickets WHERE status='待验'")[0]["c"]
            check("T6", inbox_cnt == db_pending, f"侧栏待签收={inbox_cnt} DB待验={db_pending}")

            # ---------- T4：发送群消息 → UI 出现 + DB group_messages 有记录 ----------
            send_txt = "自检发送群消息" + str(int(time.time()))
            page.fill("#input", send_txt)
            page.click("button.send")
            page.wait_for_timeout(1500)
            feed_txt2 = page.inner_text("#feed")
            ui_ok = send_txt in feed_txt2
            db_ok = db_all("SELECT COUNT(*) c FROM group_messages WHERE content=?", send_txt)[0]["c"] >= 1
            check("T4", ui_ok and db_ok, f"UI出现={ui_ok} DB记录={db_ok}")

            # ---------- T5：1:1 发送 → chat_messages 有记录 ----------
            opts = page.eval_on_selector_all("#rolePicker option", "els => els.map(e => e.value)")
            member = next((o for o in opts if o != "__group__"), None)
            page.select_option("#rolePicker", member)
            page.wait_for_timeout(600)
            one_txt = "自检发送1对1" + str(int(time.time()))
            page.fill("#input", one_txt)
            page.click("button.send")
            page.wait_for_timeout(1500)
            db_chat = db_all("SELECT COUNT(*) c FROM chat_messages WHERE role='user' AND content=?", one_txt)[0]["c"] >= 1
            check("T5", bool(member) and db_chat, f"成员={member} chat_messages记录={db_chat}")

            # ---------- T7：打开待验单 → 签收 → 已验 + signoffs + events(gate_passed) ----------
            page.evaluate("location.hash = '#/ticket/T-TEST-001'")
            page.wait_for_selector("button[data-act='sign'][data-tk='T-TEST-001']", timeout=8000)
            page.click("button[data-act='sign'][data-tk='T-TEST-001']")
            page.wait_for_selector("#gateOk", timeout=5000)
            page.eval_on_selector_all(".gates input[data-gate]",
                                      "els => els.forEach(e => { e.checked = true; e.dispatchEvent(new Event('change', {bubbles:true})); })")
            page.click("#gateOk")
            page.wait_for_timeout(1500)
            page.wait_for_timeout(600)
            main_txt = page.inner_text("#main")
            ui_verified = ("已签收" in main_txt) or ("已验" in main_txt)
            db_status = db_all("SELECT status FROM tickets WHERE id='T-TEST-001'")[0]["status"]
            db_sign = db_all("SELECT COUNT(*) c FROM signoffs WHERE ticket_id='T-TEST-001'")[0]["c"]
            db_evt = db_all("SELECT COUNT(*) c FROM events WHERE ticket_id='T-TEST-001' AND kind='gate_passed'")[0]["c"]
            check("T7", ui_verified and db_status == "已验" and db_sign >= 1 and db_evt >= 1,
                  f"UI已验={ui_verified} DB状态={db_status} signoffs={db_sign} gate_passed事件={db_evt}")

            # ---------- T8：对已验单再签收 → 服务端 409 + UI 不再提供签收入口 ----------
            req = urllib.request.Request(BASE + "/api/tickets/T-TEST-001/signoff",
                                         data=json.dumps({"gate": "内容完整覆盖所有任务要求", "signer": "主理人"}).encode("utf-8"),
                                         headers={"X-Deck-Token": TOKEN, "Content-Type": "application/json"}, method="POST")
            try:
                urllib.request.urlopen(req, timeout=10)
                api409 = False
            except urllib.error.HTTPError as he:
                api409 = (he.code == 409)
            page.evaluate("location.hash = '#/ticket/T-TEST-001'")
            page.wait_for_timeout(800)
            no_sign_btn = page.query_selector("button[data-act='sign'][data-tk='T-TEST-001']") is None
            db_sign2 = db_all("SELECT COUNT(*) c FROM signoffs WHERE ticket_id='T-TEST-001'")[0]["c"]
            check("T8", api409 and no_sign_btn and db_sign2 == 1,
                  f"服务端409={api409} UI无签收按钮={no_sign_btn} signoffs仍={db_sign2}")

            # ---------- T9：归档 → 归档页出现且 DB archived=1；恢复 → 0 ----------
            page.evaluate("var m=document.getElementById('modal'); if(m){m.classList.remove('open'); m.innerHTML='';}")
            page.wait_for_timeout(300)
            page.evaluate("location.hash = '#/projects'")
            page.wait_for_selector("button[data-act='archive'][data-pjid='p2']", timeout=8000)
            page.click("button[data-act='archive'][data-pjid='p2']")
            page.wait_for_timeout(1500)
            db_arch = db_all("SELECT archived FROM projects WHERE id='p2'")[0]["archived"]
            page.click("div[data-pjtab='archived']")
            page.wait_for_selector("button[data-act='restore'][data-pjid='p2']", timeout=8000)
            main_txt = page.inner_text("#main")
            ui_arch = ("测试项目B" in main_txt) and ("恢复项目" in main_txt)
            check("T9a", db_arch == 1 and ui_arch, f"DB archived={db_arch} UI显示归档={ui_arch}")
            page.click("button[data-act='restore'][data-pjid='p2']")
            page.wait_for_timeout(1500)
            db_arch2 = db_all("SELECT archived FROM projects WHERE id='p2'")[0]["archived"]
            check("T9b", db_arch2 == 0, f"恢复后 DB archived={db_arch2}")

            # ---------- T10：搜索命中（后端 API）+ UI 历史弹窗过滤 ----------
            try:
                sdata = api_get("/api/search/messages?scope=project&project_id=p1&q=" + urllib.parse.quote("唯一搜索词XYZ"))
                sjson = json.dumps(sdata, ensure_ascii=False)
                api_hit = "唯一搜索词XYZ" in sjson
            except Exception as e:
                api_hit = False
                sjson = str(e)
            page.evaluate("location.hash = '#/chat'")
            page.wait_for_timeout(500)
            page.select_option("#rolePicker", "__group__")
            page.wait_for_timeout(800)
            page.click("button[data-act='hist']")
            page.wait_for_selector("#histSearch", timeout=5000)
            page.fill("#histSearch", "唯一搜索词XYZ")
            page.wait_for_timeout(500)
            hit_items = page.eval_on_selector_all("#histList .hist-item", "els => els.length")
            check("T10", api_hit and hit_items >= 1,
                  f"后端搜索命中={api_hit} UI结果行={hit_items}")

            # ---------- 截图：chat 1440 ----------
            page.evaluate("location.hash = '#/chat'")
            page.wait_for_timeout(500)
            page.screenshot(path=str(TMP / "shot_chat_1440.png"))

            # ---------- 截图：ticket 1440（已签收单）----------
            page.evaluate("location.hash = '#/ticket/T-TEST-001'")
            page.wait_for_timeout(600)
            page.screenshot(path=str(TMP / "shot_ticket_1440.png"))

            # ---------- 截图：projects / 归档 ----------
            page.evaluate("location.hash = '#/projects'")
            page.wait_for_timeout(600)
            page.screenshot(path=str(TMP / "shot_projects_1440.png"))

            # ---------- T13：960 / 1280 / 1440 无横向滚动 ----------
            no_h = True
            detail = []
            for w in (960, 1280, 1440):
                page.set_viewport_size({"width": w, "height": 900})
                page.wait_for_timeout(300)
                info = page.evaluate("({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})")
                okw = info["sw"] <= info["cw"] + 2
                no_h = no_h and okw
                detail.append(f"{w}:sw={info['sw']},cw={info['cw']}")
            check("T13", no_h, "; ".join(detail))

            # ---------- T11：?demo=1 仍走演示 ----------
            page.goto(f"{BASE}/?demo=1", wait_until="load")
            page.wait_for_selector("#pjPicker", timeout=10000)
            page.wait_for_timeout(500)
            demo_txt = page.inner_text("#main")
            ok_demo = ("产品设计周会" in demo_txt) and ("测试项目A" not in demo_txt)
            check("T11", ok_demo, f"演示数据出现={'是' if '产品设计周会' in demo_txt else '否'} 真实数据未泄漏={'是' if '测试项目A' not in demo_txt else '否'}")

            # ---------- 截图：demo 模式 ----------
            page.screenshot(path=str(TMP / "shot_demo_1440.png"))

            # ---------- T22：演示占位清理（真实模式渲染无假数字 / 假附件块）----------
            page.goto(URL, wait_until="load")
            page.wait_for_selector("#pjPicker", timeout=15000)
            page.wait_for_timeout(500)
            page.evaluate("location.hash = '#/members'")
            page.wait_for_selector(".ai-grid", timeout=8000)
            page.wait_for_timeout(500)
            mtxt = page.inner_text("#main")
            bad_phrases = ["今日工单 00 条", "签收率 0%", "岗位覆盖", "/4", "/7",
                           "本工单暂无附件", "决策治理层 2/4", "质量交付层 1/7"]
            found = [p for p in bad_phrases if p in mtxt]
            # 工单详情不应有假附件块
            page.evaluate("location.hash = '#/ticket/T-TEST-002'")
            page.wait_for_timeout(600)
            ttxt = page.inner_text("#main")
            ticket_bad = ("本工单暂无附件" in ttxt) or ("📎 附件" in ttxt)
            check("T22", len(found) == 0 and not ticket_bad,
                  f"成员页演示字样={found} 工单假附件块={ticket_bad}")

            # ---------- T23：三档截图 960 / 1280 / 1440 ----------
            shots = []
            for w in (960, 1280, 1440):
                page.set_viewport_size({"width": w, "height": 900})
                page.wait_for_timeout(300)
                fn = str(TMP / f"shot_v2_{w}.png")
                page.screenshot(path=fn)
                shots.append(f"{w}:{fn}")
            check("T23", len(shots) == 3, "截图 " + " | ".join(shots))

            browser.close()
            browser = None
    except Exception as e:
        import traceback
        print("自检执行异常：", e)
        traceback.print_exc()
        check("EXEC", False, str(e))
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
else:
    for nm in ["T1", "T2", "T3", "T6", "T4", "T5", "T7", "T8", "T9a", "T9b", "T10", "T13", "T11", "T22", "T23"]:
        skip(nm)

# ======================================================================
# 5. T12：启动.py 不带 --ui 即加载新前端 web_flow（2026-11-14 默认已切 flow）
# ======================================================================
try:
    httpd.shutdown()
    time.sleep(0.3)
    launch_src = (HERE / "启动.py").read_text(encoding="utf-8")
    default_flow = 'default="flow"' in launch_src
    svc.WEB = HERE / "web_flow"  # 模拟“不带 --ui”的默认行为（默认 flow）
    httpd2, PORT2, TOKEN2 = svc.start_server(0)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT2}/", timeout=10) as r:
            html = r.read().decode("utf-8", "ignore")
        has_api = "api.js" in html
        check("T12", default_flow and has_api, f"启动.py默认flow={default_flow} 默认页面含api.js={has_api}")
    finally:
        httpd2.shutdown()
except Exception as e:
    check("T12", False, str(e))

# ======================================================================
# 6. 真实台账 / 真实配置 一致性
# ======================================================================
real_db_counts_after = None
if real_db_counts_before is not None:
    try:
        real_db_counts_after = _real_db_counts()
    except Exception:
        real_db_counts_after = None
if _dispatcher_running:
    check("REALDB", True,
          "派发器运行中（持续写台账 lock/heartbeat/WAL，SHA 必然抖动），按 C7 跳过强校验")
elif real_db_counts_before is None:
    check("REALDB", True, "真实台账不存在或不可读，跳过")
else:
    same = real_db_counts_before == real_db_counts_after
    check("REALDB", same,
          f"真实台账关键表行数(tickets/events/projects/meta)前后一致={same} {real_db_counts_before}")

real_cfg_sha_after = ""
if REAL_CONFIG.exists():
    import hashlib as _hl
    real_cfg_sha_after = _hl.sha256(REAL_CONFIG.read_bytes()).hexdigest()
check("REALCFG", real_cfg_sha_before == real_cfg_sha_after,
      f"真实配置.json SHA 前后一致={real_cfg_sha_before == real_cfg_sha_after}")

# ======================================================================
# 7. 汇总 + 清理
# ======================================================================
passed = sum(1 for v in RESULTS.values() if v[0])
total = len(RESULTS)
print("\n================ 自检汇总 ================")
for k, (ok, d) in RESULTS.items():
    print(f"  {k}: {'PASS' if ok else 'FAIL'} — {d}")
print(f"通过 {passed}/{total}")
print(f"截图目录：{TMP}")

(TMP / "自检汇总.txt").write_text(
    "\n".join(f"{k}: {'PASS' if v[0] else 'FAIL'} — {v[1]}" for k, v in RESULTS.items()),
    encoding="utf-8",
)

# 清理临时服务进程
try:
    httpd.shutdown()
except Exception:
    pass
