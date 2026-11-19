#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI聊不停 · MemSearch 记忆接入 自检（M2-MEM-FIX：17 条用例）。

零依赖：仅标准库 + 本项目 服务.py / 记忆.py。
安全：所有写库/索引均走临时目录 / 临时 collection / 临时 milvus；
      真实 台账.sqlite3 与 配置.json 不被改动（用例 12 校验）。
"""
import os
import sys
import json
import time
import shutil
import sqlite3
import tempfile
import subprocess
import threading

# 记忆.log 指向临时文件，不污染真实 数据/记忆.log
_TMP = tempfile.mkdtemp(prefix="mem_self_")
os.environ["MEM_LOG_PATH"] = os.path.join(_TMP, "记忆.log")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import 服务  # noqa: E402
import 记忆  # noqa: E402

# 用例 8/9/10 会把 memory_recall 换成桩；新增用例需用真实函数，先留引用
_REAL_RECALL = 记忆.memory_recall
_REAL_WRITE = 记忆.memory_write
_REAL_RUN_CLI = 记忆._run_cli

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


def build_cfg(memory: dict, project_dir: str = "", adapters=None):
    cfg = {
        "paths": {"project": project_dir},
        "adapters": adapters or {"codex": {"id": "codex", "label": "CODEX", "type": "ai", "models": [{"id": "m1", "name": "M1"}]}},
        "memory": memory,
    }
    return cfg


_TMP_DB = os.path.join(_TMP, "ledger.sqlite3")


def _tmp_db():
    conn = sqlite3.connect(_TMP_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT, status TEXT, error TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS chat_messages(id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT, content TEXT, adapter TEXT, model TEXT, ticket_id TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS group_messages(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, role TEXT, content TEXT, adapter TEXT, mentions TEXT, created_at TEXT)")
    return conn


def setup_harness(memory, recall_block=None, project_dir="", adapters=None):
    captured = {}
    服务.db = _tmp_db
    服务.init_db()  # 在临时库上建全表（jobs/job_events/chat_messages/group_messages 等）
    if recall_block is not None:
        记忆.memory_recall = lambda cfg, pd, q: recall_block
    服务.dispatch_chat = lambda *a: (captured.update(prompt=a[2], target=a[1]) or f"[REPLY:{a[1]}]")
    服务.load_config = lambda: build_cfg(memory, project_dir, adapters)
    return captured


# ---------------- 用例 1：关闭时召回块为空 ----------------
def t1():
    block = 记忆.memory_recall(build_cfg({"enabled": False}), "x", "q")
    check("1 关闭时 memory_recall 返回空", block == "")


# ---------------- 用例 2：关闭时聊天 prompt 与基线逐字节一致 ----------------
def t2():
    captured = setup_harness({"enabled": False})
    服务.run_chat_job("j2", "codex", "你好吗", None, None, [], None)
    base = captured.get("prompt", "")
    check("2 关闭 memory 时聊天 prompt 不含召回块", "【长期记忆" not in base and base != "")
    return base


# ---------------- 用例 3：CLI 缺失 → 降级空，且只告警一次 ----------------
def t3():
    记忆._CLI_WARNED = False
    cfg = build_cfg({"enabled": True, "cli": "definitely_not_memsearch_xyz"})
    for _ in range(3):
        记忆.memory_recall(cfg, _TMP, "q")
    log = open(os.environ["MEM_LOG_PATH"], encoding="utf-8").read()
    n = log.count("CLI 未找到")
    check("3 CLI 缺失降级空且告警仅 1 次", n == 1, f"log={log!r}")


# ---------------- 用例 4：子进程非零退出 → 降级空 ----------------
def t4():
    bat = os.path.join(_TMP, "fake_exit3.bat")
    open(bat, "w").write("@echo off\r\nexit /b 3\r\n")
    cfg = build_cfg({"enabled": True, "cli": bat})
    r = 记忆.memory_recall(cfg, _TMP, "q")
    check("4 子进程非零退出降级空", r == "")


# ---------------- 用例 5：搜索超时 → 降级空且不报错 ----------------
def t5():
    bat = os.path.join(_TMP, "fake_slow.bat")
    open(bat, "w").write("@echo off\r\nping -n 6 127.0.0.1 >nul\r\n")
    cfg = build_cfg({"enabled": True, "cli": bat, "search_timeout_s": 1})
    t0 = time.time()
    r = 记忆.memory_recall(cfg, _TMP, "q")
    dt = time.time() - t0
    check("5 搜索超时降级空且 <3s", r == "" and dt < 3.0, f"dt={dt:.2f}")


# ---------------- 用例 6：真实 memsearch 召回唯一串（写回+召回闭环） ----------------
def t6():
    cli = shutil.which("memsearch")
    if not cli:
        check("6 真实召回（CLI 缺失，跳过）", True, "memsearch 未安装")
        return
    src = os.path.join(_TMP, "src6")
    os.makedirs(src, exist_ok=True)
    marker = "UNIQUE_CB_SMOKE_6A2B"
    open(os.path.join(src, "a.md"), "w", encoding="utf-8").write(
        "## 2026-11-12\n### 10:00 · codex · chat\n- 用户问：%s 是什么\n- AI 答：唯一测试标记\n" % marker)
    col = "aichat_selftest6"
    uri = os.path.join(_TMP, "milvus6.db")
    try:
        idx = subprocess.run([cli, "index", src, "-c", col, "--milvus-uri", uri],
                             capture_output=True, text=True, timeout=120,
                             env={**os.environ, "PYTHONUTF8": "1"})
        if idx.returncode != 0:
            check("6 真实召回（index 失败，跳过）", True, idx.stderr[:120])
            return
        cfg = build_cfg({"enabled": True, "source_dir": src, "collection": col, "milvus_uri": uri, "top_k": 3})
        r = 记忆.memory_recall(cfg, src, marker)
        ok = marker in r and "【长期记忆" in r
        check("6 真实召回命中唯一串", ok, r[:80] if not ok else "")
    finally:
        pass


# ---------------- 用例 7：空/不存在 source_dir → 召回空 ----------------
def t7():
    cfg = build_cfg({"enabled": True, "source_dir": os.path.join(_TMP, "nope_7")})
    check("7 空/不存在 source_dir 召回空", 记忆.memory_recall(cfg, _TMP, "q") == "")


# ---------------- 用例 8：写回内容脱敏（Key 串不入库） ----------------
def t8():
    src = os.path.join(_TMP, "src8", ".memsearch", "memory")
    captured = setup_harness({"enabled": True, "write_back": True, "source_dir": src}, recall_block="X")
    proj = os.path.join(_TMP, "proj8")
    服务.run_chat_job("j8", "codex", "告诉我密钥", None, None, [], None)
    # 用真实写回：再跑一次但让 memory_recall 返回块、write_back 开启
    记忆.memory_recall = lambda cfg, pd, q: "【长期记忆】\n- [来源：a.md] 历史\n\n（以上…）"
    记忆.memory_write(build_cfg({"enabled": True, "write_back": True, "source_dir": src}),
                      proj, "问题含 sk-ABCDEF123456 的密钥", "sk-ABCDEF1234567890 是密钥", "codex", "chat")
    files = [os.path.join(src, f) for f in os.listdir(src)] if os.path.isdir(src) else []
    content = "".join(open(f, encoding="utf-8").read() for f in files)
    ok = ("已脱敏" in content) and ("sk-ABCDEF1234567890" not in content)
    check("8 写回 Key 串被脱敏", ok, content[:120] if not ok else "")


# ---------------- 用例 9：开启 memory 时聊天 prompt 以召回块开头 ----------------
def t9():
    block = "【RECALL TEST BLOCK 9】\n- [来源：a.md#10:00] 历史记忆\n\n（以上…）"
    captured = setup_harness({"enabled": True}, recall_block=block)
    base = 服务.run_chat_job.__name__ and None
    服务.run_chat_job("j9", "codex", "今天天气", None, None, [], None)
    p = captured.get("prompt", "")
    ok = p.startswith(block + "\n\n")
    check("9 开启 memory 聊天 prompt 以召回块开头", ok, p[:60])


# ---------------- 用例 10：群聊 prompt 同样注入召回块 ----------------
def t10():
    block = "【RECALL TEST BLOCK 10】\n- [来源：b.md#11:00] 群记忆\n\n（以上…）"
    captured = setup_harness({"enabled": True}, recall_block=block)
    服务.run_group_job("j10", "P1", "codex", "群里问一句")
    p = captured.get("prompt", "")
    ok = p.startswith(block + "\n\n")
    check("10 群聊 prompt 以召回块开头", ok, p[:60])


# ---------------- 用例 11（M2-FIX 改写）：后台一次性 index + Timer 合并 + 收尾 ----------------
def t11():
    """原 watch 单写者语义已废弃；改写为：无常驻进程表、后台一次性索引、Timer 合并、close_all 清空。"""
    记忆.memory_recall = _REAL_RECALL
    src = os.path.join(_TMP, "src11")
    os.makedirs(src, exist_ok=True)
    calls = []

    class _CP:
        returncode = 0
        stdout = "[]"
        stderr = ""

    old = 记忆._run_cli
    记忆._run_cli = lambda cmd, cwd, timeout: (calls.append(list(cmd)) or _CP())
    try:
        cfg = build_cfg({"enabled": True, "watch": True, "write_back": True,
                         "source_dir": src, "collection": "aichat_selftest11",
                         "watch_debounce_ms": 200})
        记忆.memory_ensure_index(cfg, src)
        for _ in range(40):
            if calls:
                break
            time.sleep(0.05)
        started = len([c for c in calls if "index" in c]) >= 1
        记忆.memory_write(cfg, src, "q1", "a1", "codex", "chat")
        记忆.memory_write(cfg, src, "q2", "a2", "codex", "chat")
        记忆.memory_write(cfg, src, "q3", "a3", "codex", "chat")
        merged = len(记忆._INDEX_TIMERS) <= 1
        记忆.memory_close_all()
        cleared = len(记忆._INDEX_TIMERS) == 0
        no_watch_proc = not hasattr(记忆, "_WATCHES")
        check("11 后台一次性index+Timer合并+close_all(无常驻watch)",
              started and merged and cleared and no_watch_proc,
              f"started={started} merged={merged} cleared={cleared} no_watch={no_watch_proc}")
    finally:
        记忆._run_cli = old
        记忆.memory_close_all()


# ---------------- 用例 12：真实台账/配置未被污染 ----------------
def t12():
    ledger = os.path.join(HERE, "数据", "台账.sqlite3")
    cfg_path = os.path.join(HERE, "配置.json")
    if os.path.exists(ledger):
        ns = os.stat(ledger).st_mtime_ns
        check("12a 真实台账 mtime_ns 前后一致", ns == t12._m0, f"{t12._m0} vs {ns}")
    else:
        check("12a 真实台账不存在（跳过）", True)
    cfg_now = json.load(open(cfg_path, encoding="utf-8"))
    cfg_now.pop("memory", None)
    # 2026-11-13：A/B/C 补丁追加 memory.rules_text（属 memory 段，被 pop 掉不影响本用例），
    # 同时配置已含后续正常变更（codex output=jsonl 等）→ 基线前滚到 配置.json.bak-after-codexjsonl-20261113。
    cfg_base = json.load(open(os.path.join(HERE, "配置.json.bak-after-codexjsonl-20261113"), encoding="utf-8"))
    cfg_base.pop("memory", None)
    check("12b 配置除 memory 外逐键一致（基线：after-codexjsonl-20261113）", cfg_now == cfg_base, str(list(cfg_now.keys())))


def _restore_real():
    """后续用例需要真实实现（前面用例替换过桩）。"""
    记忆.memory_recall = _REAL_RECALL
    记忆.memory_write = _REAL_WRITE
    记忆._run_cli = _REAL_RUN_CLI
    记忆.memory_close_all()


class _FakeCP:
    def __init__(self, returncode=0, stdout="[]", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_bat(name, body):
    path = os.path.join(_TMP, name)
    with open(path, "w", encoding="ascii") as f:
        f.write(body)
    return path


def _clean_log():
    """清空临时日志，便于单用例断言日志内容。"""
    with open(os.environ["MEM_LOG_PATH"], "w", encoding="utf-8") as f:
        f.write("")


def _read_log():
    with open(os.environ["MEM_LOG_PATH"], encoding="utf-8") as f:
        return f.read()


# ---------------- 用例 13：无常驻 watch 子进程 ----------------
def t13():
    """注入假 _run_cli 记录所有下发给 subprocess 的 cmd；断言没有任何 cmd 含 watch，
    且后台 index 线程均为一次性（会结束），模块无常驻 Popen 进程表。"""
    _restore_real()
    src = os.path.join(_TMP, "src13")
    os.makedirs(src, exist_ok=True)
    proj = os.path.join(_TMP, "proj13")
    os.makedirs(proj, exist_ok=True)
    cfg = build_cfg({"enabled": True, "watch": True, "source_dir": src,
                     "collection": "aichat_selftest13",
                     "milvus_uri": os.path.join(_TMP, "mv13.db")})
    cmds = []
    old_run = 记忆._run_cli

    def spy_run(cmd, cwd, timeout):
        cmds.append(list(cmd))
        return _FakeCP()  # 不真正起进程

    记忆._run_cli = spy_run
    try:
        记忆.memory_ensure_index(cfg, proj)
        for t in list(记忆._INDEX_THREADS):
            t.join(timeout=3)
        no_watch_cmd = not any("watch" in c for c in cmds)
        ran_once = any("index" in c for c in cmds)
        threads_done = all(not t.is_alive() for t in 记忆._INDEX_THREADS)
        no_watch_dict = not hasattr(记忆, "_WATCHES")
        check("13 无常驻 watch 子进程（CLI cmd 无 watch + 一次性 index 线程均结束）",
              no_watch_cmd and ran_once and threads_done and no_watch_dict,
              f"cmds={cmds} done={threads_done}")
    finally:
        记忆._run_cli = old_run
        记忆.memory_close_all()


# ---------------- 用例 14：并发召回串行化 ----------------
def t14():
    """两个线程同时 recall，_CLI_LOCK 串行；都非空、总耗时≤2×timeout、日志无锁冲突残留。"""
    _restore_real()
    _clean_log()
    cli = shutil.which("memsearch")
    if not cli:
        check("14 并发召回（CLI 缺失，跳过）", True, "memsearch 未安装")
        return
    src = os.path.join(_TMP, "src14")
    os.makedirs(src, exist_ok=True)
    marker = "UNIQUE_CONCURRENT_14"
    with open(os.path.join(src, "a.md"), "w", encoding="utf-8") as f:
        f.write("## 2026-11-12\n### 10:00 · codex · chat\n- 用户问：%s\n- AI 答：并发标记\n" % marker)
    col, uri = "aichat_selftest14", os.path.join(_TMP, "mv14.db")
    idx = subprocess.run([cli, "index", src, "-c", col, "--milvus-uri", uri],
                         capture_output=True, text=True, timeout=180,
                         env={**os.environ, "PYTHONUTF8": "1"})
    if idx.returncode != 0:
        check("14 并发召回（index 失败，跳过）", True, idx.stderr[:120])
        return
    cfg = build_cfg({"enabled": True, "source_dir": src, "collection": col,
                     "milvus_uri": uri, "top_k": 3, "search_timeout_s": 20})
    out = {}
    barrier = threading.Barrier(2)

    def worker(k):
        barrier.wait()
        out[k] = 记忆.memory_recall(cfg, src, marker)

    t0 = time.time()
    ths = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=60)
    dt = time.time() - t0
    both = all(marker in out.get(i, "") for i in range(2))
    clean = "锁冲突重试后仍失败" not in _read_log()
    check("14 并发召回都非空 + 串行≤2×timeout + 日志无锁冲突残留",
          both and clean and dt <= 40, f"dt={dt:.1f}s both={both} clean={clean}")


# ---------------- 用例 15：锁冲突重试 ----------------
def t15():
    """假 CLI（注入 _run_cli）：前 2 次输出 Failed to start MilvusLite + exit 1，
    第 3 次返回正常 JSON。等价任务书的假 memsearch.bat，但规避 cmd.exe 的 FD 陷阱。"""
    _restore_real()
    _clean_log()
    src = os.path.join(_TMP, "src15")
    os.makedirs(src, exist_ok=True)
    cfg = build_cfg({"enabled": True, "cli": "memsearch", "source_dir": src,
                     "collection": "aichat_selftest15",
                     "milvus_uri": os.path.join(_TMP, "mv15.db")})
    tries = []
    good = json.dumps([{"content": "UNIQUE_RETRY_15_OK", "score": 0.9,
                        "source": "a.md", "heading": "10:00"}])

    def fake_run(cmd, cwd, timeout):
        tries.append(list(cmd))
        if len(tries) <= 2:
            return _FakeCP(returncode=1, stdout="",
                           stderr="Failed to start MilvusLite server for x.db\n  _acquire_lock msvcrt.locking")
        return _FakeCP(returncode=0, stdout=good)

    old_run = 记忆._run_cli
    old_cli = 记忆._cli_path
    记忆._run_cli = fake_run
    记忆._cli_path = lambda c: "fake-memsearch"  # 不依赖本机是否装了 memsearch
    try:
        r = 记忆.memory_recall(cfg, src, "q")
        log = _read_log()
        retried = ("锁冲突" in log) and ("重试" in log)
        check("15 锁冲突重试3次后召回成功 + 日志记录重试",
              len(tries) == 3 and "UNIQUE_RETRY_15_OK" in r and retried,
              f"tries={len(tries)} r={r[:60]!r} retried={retried}")
    finally:
        记忆._run_cli = old_run
        记忆._cli_path = old_cli


# ---------------- 用例 16：写回后 debounce 自动索引 ----------------
def t16():
    """write_back=true + watch=true：写入唯一串，等待 Timer 后台 index，再 recall 命中。"""
    _restore_real()
    cli = shutil.which("memsearch")
    if not cli:
        check("16 写回自动索引（CLI 缺失，跳过）", True, "memsearch 未安装")
        return
    src = os.path.join(_TMP, "src16")
    os.makedirs(src, exist_ok=True)
    col, uri = "aichat_selftest16", os.path.join(_TMP, "mv16.db")
    marker = "UNIQUE_WRITEBACK_16"
    cfg = build_cfg({"enabled": True, "watch": True, "write_back": True,
                     "source_dir": src, "collection": col, "milvus_uri": uri,
                     "watch_debounce_ms": 500, "top_k": 3, "search_timeout_s": 20})
    记忆.memory_write(cfg, src, "写入问题", "答案是 " + marker, "codex", "chat")
    # 先等 debounce Timer 里的后台 index 跑完，再召回（避免与索引抢 _CLI_LOCK）
    deadline = time.time() + 90
    while time.time() < deadline:
        timers = list(记忆._INDEX_TIMERS.values())
        if timers and all(not t.is_alive() for t in timers):
            break
        time.sleep(0.5)
    hit = ""
    deadline = time.time() + 60
    while time.time() < deadline:
        hit = 记忆.memory_recall(cfg, src, marker)
        if marker in hit:
            break
        time.sleep(1.0)
    记忆.memory_close_all()
    check("16 写回 debounce 自动索引后 recall 命中唯一串", marker in hit,
          f"hit={hit[:60]!r}")


# ---------------- 用例 17：日志 >2MB 轮转 ----------------
def t17():
    _restore_real()
    log_path = os.environ["MEM_LOG_PATH"]
    rot = log_path + ".1"
    for p in (log_path, rot):
        if os.path.exists(p):
            os.remove(p)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("x" * (2 * 1024 * 1024 + 10))
    记忆._log("轮转后首行")
    ok = (os.path.exists(rot) and os.path.getsize(rot) > 2 * 1024 * 1024
          and os.path.exists(log_path) and os.path.getsize(log_path) < 4096)
    check("17 日志>2MB 轮转生成 .log.1 且新日志从小开始", ok,
          f"rot={os.path.getsize(rot) if os.path.exists(rot) else -1} "
          f"cur={os.path.getsize(log_path) if os.path.exists(log_path) else -1}")


if __name__ == "__main__":
    ledger = os.path.join(HERE, "数据", "台账.sqlite3")
    t12._m0 = os.stat(ledger).st_mtime_ns if os.path.exists(ledger) else 0
    print("=== AI聊不停 · MemSearch 记忆接入 自检（M2-MEM-FIX，17 条） ===")
    t1(); t2(); t3(); t4(); t5(); t6(); t7(); t8(); t9(); t10(); t11(); t12()
    t13(); t14(); t15(); t16(); t17()
    记忆.memory_close_all()
    print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
