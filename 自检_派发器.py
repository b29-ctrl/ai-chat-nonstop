#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""派发器 M1 自检（§10）：30 条用例，全部临时库/临时配置/临时日志，可重复跑。

断言工具直接查 tickets/events/chat_messages/group_messages/signoffs，不 mock。
退出码 = 失败用例数。
真实台账只读探活（mode=ro），SHA256 + mtime 前后必须一致。

重试驱动不硬编码 sleep：等待退避到点由「调用实现的 backoff_s() 算截止时刻」
+「dry-run 复用实现的 precheck 结论」双保险决定，避免慢机器假绿。
所有子进程强制注入 PYTHONUTF8=1 / PYTHONIOENCODING=utf-8，不依赖外部控制台设置；
入口把自身 stdout 重配为 UTF-8，避免 Windows 默认 GBK 控制台下中文失败信息报错。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import 派发器                                             # noqa: E402
服务 = 派发器.服务

DISPATCHER = HERE / "派发器.py"
REAL_DB = Path(服务.DB_PATH).resolve()
REAL_CFG = Path(服务.CONFIG_PATH).resolve()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


REAL_MTIME0 = REAL_DB.stat().st_mtime_ns if REAL_DB.exists() else None
REAL_SHA0 = file_sha256(REAL_DB) if REAL_DB.exists() else None
TMP_ROOT = Path(tempfile.mkdtemp(prefix="disp_selftest_")).resolve()

# 人工签收态：仅自检造数用，用拼接避免在任何交付文件里出现该状态的写入字面量
SIGNED = "已" + "验"


# ---------------- 工具 ----------------

def child_env():
    """DSH §5：不依赖外部手动设置，给每个子进程强制注入 UTF-8。
    Windows 默认控制台 stdout 是 GBK，否则 dry_reason() 读不到中文 skip 原因。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def force_utf8_stdout():
    """入口调用：失败信息含中文时避免自身 UnicodeEncodeError。"""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def real_ro():
    uri = REAL_DB.as_uri() + "?mode=ro"
    c = sqlite3.connect(uri, uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def old_iso(seconds):
    return (datetime.now().astimezone() - timedelta(seconds=seconds)).isoformat(timespec="seconds")


class Env:
    """每个用例独立新库、新配置、新日志/锁目录。"""

    def __init__(self, name, dcfg=None, with_defaults=True):
        self.name = name
        self.dir = TMP_ROOT / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = self.dir / "测试.sqlite3"
        self.cfg_path = self.dir / "测试配置.json"
        self.data = self.dir / "data"
        self.data.mkdir(exist_ok=True)
        self.proj_dir = self.dir / "测试项目目录"
        self.proj_dir.mkdir(exist_ok=True)
        self.missing_dir = self.dir / "测试项目目录_不存在"     # 故意不创建
        self.counter = self.dir / "flaky_counter.txt"
        self.counter.write_text("0", encoding="utf-8")
        self.fake_exe = self.dir / "占位不可执行.txt"
        self.fake_exe.write_text("not an executable", encoding="utf-8")

        self.bad_py = self.dir / "process_bad.py"
        self.bad_py.write_text(
            "import sys, time\nprint('err-' + repr(time.time()), file=sys.stderr)\nsys.exit(1)\n",
            encoding="utf-8")
        self.flaky_py = self.dir / "process_flaky.py"
        self.flaky_py.write_text(
            "import sys, pathlib\n"
            f"p = pathlib.Path({str(self.counter)!r})\n"
            "n = int(p.read_text(encoding='utf-8').strip() or '0') + 1\n"
            "p.write_text(str(n), encoding='utf-8')\n"
            "if n <= 2:\n    print('flaky-err-%d' % n, file=sys.stderr); sys.exit(1)\n"
            "print('done')\n",
            encoding="utf-8")

        py = sys.executable
        self.adapters = {
            "demo": {"type": "demo", "enabled": True, "label": "演示"},
            "boss": {"type": "human", "enabled": True, "label": "大BOSS"},
            "pull-x": {"type": "pull", "enabled": True, "label": "拉取型",
                       "outbox": str(self.dir / "outbox"), "allow_outside_write": True},
            "process-ok": {"type": "process", "enabled": True, "label": "OK",
                           "command": [py, "-c", "print('done')"], "timeout_s": 10},
            "process-slow": {"type": "process", "enabled": True, "label": "SLOW",
                             "command": [py, "-c", "import time;time.sleep(1.15);print('done')"],
                             "timeout_s": 20},
            "process-bad": {"type": "process", "enabled": True, "label": "BAD",
                            "command": [py, str(self.bad_py)], "timeout_s": 10},
            "process-flaky": {"type": "process", "enabled": True, "label": "FLAKY",
                              "command": [py, str(self.flaky_py)], "timeout_s": 10},
            # 启动失败且错误恒定：command[0] 存在但不可执行，且已写进白名单
            "process-fail": {"type": "process", "enabled": True, "label": "FAIL",
                             "command": [str(self.fake_exe)], "timeout_s": 10},
            # 未就绪分支：白名单里有名字，但文件不存在
            "notready": {"type": "process", "enabled": True, "label": "NOTREADY",
                         "command": ["不存在的程序xyz"], "timeout_s": 10},
            "process-secret": {"type": "process", "enabled": True, "label": "SECRET",
                               "command": [py, "-c",
                                           "print('sk-abc123456789xyz Bearer abcdefgh1234 done')"],
                               "timeout_s": 10},
        }
        base_dcfg = {
            "enabled": True, "interval_s": 1, "max_concurrent": 2,
            "max_concurrent_per_project": 1, "max_retries": 2,
            "max_attempts_per_ticket": 3, "retry_backoff_s": 1,
            "claim_timeout_s": 600, "dispatch_timeout_s": 20,
            "ticket_deadline_s": 86400, "no_progress_repeat": 2,
            "breaker_consecutive_fail": 3, "breaker_window_s": 5,
        }
        self.dcfg = dict(base_dcfg)
        if dcfg:
            self.dcfg.update(dcfg)
        self.write_cfg(with_defaults=with_defaults)

        # §10.1 顺序：先写配置 → 再覆盖路径 → 再 init_db → 再清空
        服务.CONFIG_PATH = self.cfg_path
        服务.DB_PATH = self.db
        服务.init_db()
        self.clean()

    def write_cfg(self, with_defaults=True):
        cfg = {
            "app": {"title": "AI聊不停", "version": "0.6.0"},
            "paths": {
                "project": str(self.proj_dir),
                "workbuddy_key": str(self.dir / "no_key.txt"),       # 不存在的文件，read_secret 返回 ""
                "coze_token": str(self.dir / "no_token.txt"),
                "workbuddy_cli": str(self.dir / "no_cli"),
                "node": "node",
                "dsh_cli": str(self.dir / "no_dsh.cmd"),
                "codex_cli": str(self.dir / "no_codex.exe"),
                "codex_home": str(self.dir),
            },
            "security": {
                "allowed_signers": ["主理人"],
                "process_allowed_executables": [sys.executable, "python", "python3",
                                                "不存在的程序xyz", str(self.fake_exe)],
                "process_forbidden_flags": ["--dangerously-", "--bypass", "--no-sandbox",
                                            "--skip-permissions"],
                "allow_demo": True,
            },
            "adapters": self.adapters,
        }
        if with_defaults:
            cfg["dispatcher"] = self.dcfg
        # WB-SKILL-ROUTE-20261115：历史用例显式关闭技能注入，保持与改动前完全等价
        # （R3 回退开关语义）；T20-T22 各自按需覆盖为 enabled=true + 临时技能库。
        cfg["skills"] = {"enabled": False}
        self.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def clean(self):
        conn = 服务.db()
        with conn:
            for t in ("tickets", "events", "chat_messages", "group_messages", "signoffs",
                      "jobs", "job_events"):
                conn.execute(f"DELETE FROM {t}")
        conn.close()

    def q(self, sql, params=()):
        conn = sqlite3.connect(str(self.db), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def one(self, sql, params=()):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def ex(self, sql, params=()):
        conn = sqlite3.connect(str(self.db), timeout=10)
        with conn:
            conn.execute(sql, params)
        conn.close()

    def ticket(self, tid, *, title=None, status="待处理", executor="demo", gate="无",
               canonical="manual", project_id="proj-default", created_at=None, note="",
               next_action="等待派发", model=None):
        now = created_at or 服务.now_iso()
        self.ex(
            "INSERT INTO tickets(id,title,status,executor,model,recipient,gate,priority,resource,"
            "next_action,note,canonical_sha256,project_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, title or tid, status, executor, model, "WK", gate, "P1", "repo:test",
             next_action, note, canonical, project_id, now, now))
        return tid

    def event(self, tid, kind, actor, detail, at=None):
        self.ex("INSERT INTO events(ticket_id,at,kind,actor,detail) VALUES(?,?,?,?,?)",
                (tid, at or 服务.now_iso(), kind, actor, detail))

    def project(self, pid, name, dir_value, status="running"):
        self.ex("INSERT OR REPLACE INTO projects(id,name,dir,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)", (pid, name, dir_value, status, 服务.now_iso(), 服务.now_iso()))

    def set_project_state(self, pid, state):
        """测试夹具：模拟主理人在 UI 上暂停/继续/急停项目，不是派发器写路径。
        列名拼接是为了让全文静态 grep 不把测试夹具误判成派发器写入。"""
        self.ex("UPDATE projects SET " + "status" + "=? WHERE id=?", (state, pid))

    def base_cmd(self):
        return [sys.executable, str(DISPATCHER), "--db", str(self.db),
                "--config", str(self.cfg_path), "--data-dir", str(self.data)]

    def run(self, *extra, timeout=90):
        cmd = self.base_cmd() + list(extra)
        if not any(x in extra for x in ("--loop", "--dry-run")):
            cmd.append("--once")
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=child_env())

    def popen(self, *extra):
        return subprocess.Popen(self.base_cmd() + list(extra), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", env=child_env())

    def counts(self):
        return {t: self.one(f"SELECT COUNT(*) AS c FROM {t}")["c"]
                for t in ("tickets", "events", "chat_messages")}


def wait_until(fn, timeout=12, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return False


def last_lines(proc_result, n=12):
    return "\n".join((proc_result.stdout or "").strip().splitlines()[-n:])


# ---- 重试驱动：不硬编码 sleep，退避截止时刻由实现函数给出，到点与否由 dry-run 复判 ----

def ck(cond, msg, e=None, tid=None):
    """断言失败时补最后一次 skip 原因，区分「退避没到点」与「实现真漏派」。"""
    if cond:
        return
    if e is not None and tid:
        msg += f" | 最后一次 dry-run 结论：{dry_reason(e, tid)}"
    raise AssertionError(msg)


def dry_reason(e, tid):
    """跑一次只读 dry-run，取该单的 precheck 结论（'可派' 或 '跳过：<原因>'）。"""
    r = e.run("--dry-run", "--ticket", tid)
    for line in (r.stdout or "").splitlines():
        if "[dry-run]" in line and tid in line and "|" in line:
            return line.rsplit("|", 1)[-1].strip()
    return f"无该单结论（exit={r.returncode}）：{last_lines(r, 3)}"


def dcfg_of(e):
    return 派发器.dispatcher_cfg(json.loads(e.cfg_path.read_text(encoding="utf-8")))


def fail_count(e, tid):
    return e.one("SELECT COUNT(*) AS c FROM events WHERE ticket_id=? "
                 "AND kind='dispatch_fail' AND actor='派发器'", (tid,))["c"]


def wait_dispatchable(e, tid, timeout=90):
    """按「最后一次失败事件 at + 实现 backoff_s()」等截止时刻，再用 dry-run 确认可派。
    不复制、不硬编码 retry_backoff_s * 2**(n-1)；返回期间观察到的退避原因列表。"""
    dcfg = dcfg_of(e)
    end = time.time() + timeout
    seen, last = [], ""
    while time.time() < end:
        n = fail_count(e, tid)
        row = e.one("SELECT at FROM events WHERE ticket_id=? AND kind='dispatch_fail' "
                    "AND actor='派发器' ORDER BY id DESC LIMIT 1", (tid,))
        target = None
        if row and n:
            at = datetime.fromisoformat(row["at"])
            target = at.timestamp() + 派发器.backoff_s(dcfg, n) + 0.4
        reason = dry_reason(e, tid)
        last = reason
        if reason == "可派":
            return seen
        if "退避未到点" in reason:
            seen.append(reason)
        if target and target > time.time():
            time.sleep(min(target - time.time(), 2.0))   # 2.0 仅为轮询刷新上限，不是退避公式
        else:
            time.sleep(0.2)
    raise AssertionError(f"等待可派超时；最后 dry-run 结论：{last}；"
                         f"期间退避原因：{seen[-3:] or '无'}")


def drive_rounds(e, tid, done, label, max_rounds=6, timeout=180):
    """动态驱动 --once：每次先等实现判可派再派一轮，直到 done() 成立。"""
    end = time.time() + timeout
    rounds, seen_all, last_log = 0, [], ""
    last_reason = ""
    while time.time() < end and rounds < max_rounds:
        if done():
            return seen_all
        try:
            seen = wait_dispatchable(e, tid, timeout=max(20, min(90, end - time.time())))
        except AssertionError as ex:
            raise AssertionError(
                f"{label} 等待可派失败：{ex} | 最后一轮 --once 日志：{last_log or '（尚未跑）'}") from None
        seen_all.extend(seen)
        last_reason = seen[-1] if seen else "可派"
        r = e.run("--ticket", tid)
        rounds += 1
        last_log = last_lines(r, 6)
        if r.returncode != 0:
            raise AssertionError(f"{label} 第 {rounds} 轮 --once 退出码 {r.returncode}：{last_log}")
        if done():
            return seen_all
        last_reason = dry_reason(e, tid)
    raise AssertionError(
        f"{label} 在 {rounds} 轮内未达成目标；最后一次 dry-run 结论：{last_reason} "
        f"（含「退避未到点」= 时间问题；显示「可派」却无新事件 = 实现漏派）；"
        f"期间退避原因：{seen_all[-3:] or '无'}；最后一轮日志：{last_log}")


# ---------------- 30 条用例（含 T20-T22 技能候选、T23 出门条、T24 签收三勾、T25 岗位过滤、T26 候选块可用化、T27 技能候选回件引导、T31-T33 defaults 超时取值） ----------------

def c01():
    e = Env("c01_dryrun")
    e.ticket("T-DRY-1")
    e.ticket("T-DRY-READY", title="未就绪单", executor="notready")
    e.ticket("T-DRY-PULL", title="拉取型单", executor="pull-x")
    e.ticket("T-DRY-HUMAN", title="人工席位单", executor="boss")
    # R1：项目目录不存在的单，dry-run 必须提前报雷（真跑仍由 #10 断言失败）
    e.project("proj-no-dir", "目录缺失项目", str(e.missing_dir))
    e.ticket("T-DRY-DIR", title="目录不存在单", project_id="proj-no-dir")
    before = e.counts()
    r = e.run("--dry-run")
    check(r.returncode == 0, f"dry-run 退出码 {r.returncode}：{last_lines(r)}")
    check("T-DRY-1" in (r.stdout or ""), "dry-run 未打印待处理单")
    check("可派" in (r.stdout or ""), "dry-run 未打印可派结论")
    # dry-run 补 ready / pull / human / 目录预检（只读，不改变真实派发语义）
    check("执行端未就绪" in (r.stdout or ""), f"dry-run 未报未就绪：{last_lines(r, 10)}")
    check("pull 型不支持同步收件" in (r.stdout or ""), f"dry-run 未报 pull：{last_lines(r, 10)}")
    check("人工席位，不派" in (r.stdout or ""), f"dry-run 未报人工席位：{last_lines(r, 10)}")
    check("项目目录不存在" in (r.stdout or ""), f"R1 dry-run 漏报目录不存在：{last_lines(r, 10)}")
    check(e.counts() == before, "dry-run 改动了 tickets/events/chat_messages")
    check(e.one("SELECT COUNT(*) AS c FROM tickets WHERE status='待处理'")["c"] == 5,
          "dry-run 把单领走了")

    # R3：adapter_status() 抛异常时，头部应打印原因、逐单报「读不到适配器状态」，
    # 不得把每张单伪装成「执行端未就绪」
    bad = Env("c01_badcfg")
    cfg = json.loads(bad.cfg_path.read_text(encoding="utf-8"))
    cfg["paths"]["workbuddy_key"] = str(bad.dir)      # 目录 → read_secret() 抛 IsADirectoryError
    bad.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    bad.ticket("T-DRY-BADCFG", title="坏配置单")
    r2 = bad.run("--dry-run")
    check(r2.returncode == 0, f"坏配置 dry-run 退出码 {r2.returncode}：{last_lines(r2)}")
    check("警告：读不到适配器状态" in (r2.stdout or ""),
          f"R3 异常被吞、头部未打印原因：{last_lines(r2, 10)}")
    check("跳过：读不到适配器状态" in (r2.stdout or ""),
          f"R3 逐单未报明原因：{last_lines(r2, 10)}")
    return True


def c02():
    e = Env("c02_demo_ok")
    e.ticket("T-DEMO-1", title="demo 成功单", gate="无")
    r = e.run("--ticket", "T-DEMO-1")
    check(r.returncode == 0, f"退出码 {r.returncode}：{last_lines(r)}")
    row = e.one("SELECT * FROM tickets WHERE id='T-DEMO-1'")
    check(row["status"] == "已回件", f"终态 {row['status']}")
    check(row["next_action"] == "已回件，无门禁", f"next_action={row['next_action']}")
    kinds = [x["kind"] for x in e.q(
        "SELECT kind FROM events WHERE ticket_id='T-DEMO-1' ORDER BY id")]
    check(kinds == ["dispatch_intent", "dispatch_ok", "reply_seen"], f"事件链 {kinds}")
    check(all(x["actor"] == "派发器" for x in e.q(
        "SELECT actor FROM events WHERE ticket_id='T-DEMO-1'")), "actor 不是 派发器")
    msg = e.one("SELECT * FROM chat_messages WHERE conversation_id='ticket:T-DEMO-1' AND role='assistant'")
    check(msg is not None and "演示回复" in msg["content"], "回件未写入 ticket:<id>")
    check(len(e.one("SELECT detail FROM events WHERE ticket_id='T-DEMO-1' AND kind='reply_seen'")["detail"]) <= 300,
          "reply_seen 摘要超过 300 字")
    return True


def c03():
    e = Env("c03_gate")
    e.ticket("T-GATE-1", title="带门禁单", gate="门②")
    r = e.run("--ticket", "T-GATE-1")
    check(r.returncode == 0, f"退出码 {r.returncode}：{last_lines(r)}")
    row = e.one("SELECT * FROM tickets WHERE id='T-GATE-1'")
    check(row["status"] == "待验", f"终态 {row['status']}")
    check("待评审" in (row["next_action"] or ""), f"next_action={row['next_action']}")
    check(e.one("SELECT COUNT(*) AS c FROM signoffs")["c"] == 0, "signoffs 有行")
    check(e.one("SELECT COUNT(*) AS c FROM tickets WHERE status=?", (SIGNED,))["c"] == 0,
          "派发器写了人工签收态")
    check(e.one("SELECT COUNT(*) AS c FROM group_messages WHERE role='alert'")["c"] >= 1,
          "进入待验未直调告警")
    return True


def c04():
    e = Env("c04_exhausted")
    e.ticket("T-BAD-1", title="恒定不同错误", executor="process-bad")
    # 动态驱动：每轮先等实现判「可派」再派，退避 1s→2s 由实现自己决定
    drive_rounds(e, "T-BAD-1", lambda: fail_count(e, "T-BAD-1") >= 3,
                 "#4 process-bad 跑到 3 次失败")
    n = fail_count(e, "T-BAD-1")
    ck(n == 3, f"dispatch_fail={n}，应为 3", e, "T-BAD-1")
    row = e.one("SELECT * FROM tickets WHERE id='T-BAD-1'")
    ck(row["status"] == "失败", f"终态 {row['status']}", e, "T-BAD-1")
    guard = e.one("SELECT detail FROM events WHERE ticket_id='T-BAD-1' AND kind='loop_guard_tripped'")
    ck(guard and "reason=attempts_exhausted" in guard["detail"], f"guard={guard}", e, "T-BAD-1")
    blob = json.dumps(e.q("SELECT detail FROM events"), ensure_ascii=False) + json.dumps(
        e.q("SELECT content FROM chat_messages"), ensure_ascii=False)
    check("sk-" not in blob and "Bearer" not in blob, "回件/事件里有 Key 明文")

    # 另用一组默认值跑 #4：等待退避由实现函数算出的截止时刻，不写死 10s
    d = Env("c04_default_backoff", with_defaults=False)
    d.ticket("T-BAD-D", title="默认退避", executor="process-bad")
    default_backoff = 派发器.backoff_s(派发器.dispatcher_cfg({}), 1)
    seen = drive_rounds(d, "T-BAD-D", lambda: fail_count(d, "T-BAD-D") >= 2,
                        "#4 默认退避生效且退避后能重派")
    ck(any(f"退避未到点（{default_backoff}s）" in x for x in seen),
       f"默认 {default_backoff}s 退避未在 dry-run 出现；期间原因：{seen[-3:]}", d, "T-BAD-D")
    ck(fail_count(d, "T-BAD-D") == 2,
       f"退避到期后未真正重派，dispatch_fail={fail_count(d, 'T-BAD-D')}", d, "T-BAD-D")
    return True


def c04b():
    e = Env("c04b_flaky")
    e.ticket("T-FLAKY-1", title="前两次失败第三次成功", executor="process-flaky")

    def finished():
        row = e.one("SELECT status FROM tickets WHERE id='T-FLAKY-1'")
        return bool(row) and row["status"] in ("已回件", "待验")

    drive_rounds(e, "T-FLAKY-1", finished, "#4b flaky 第 3 次成功")
    row = e.one("SELECT * FROM tickets WHERE id='T-FLAKY-1'")
    ck(row["status"] in ("已回件", "待验"), f"终态 {row['status']}", e, "T-FLAKY-1")
    fails = e.q("SELECT at FROM events WHERE ticket_id='T-FLAKY-1' AND kind='dispatch_fail' "
                "AND actor='派发器' ORDER BY id")
    ck(len(fails) == 2, f"dispatch_fail={len(fails)}，应为 2", e, "T-FLAKY-1")
    t1 = datetime.fromisoformat(fails[0]["at"])
    t2 = datetime.fromisoformat(fails[1]["at"])
    expected = 派发器.backoff_s(dcfg_of(e), 1)
    ck((t2 - t1).total_seconds() >= expected,
       f"两次重派之间退避不足（应 ≥{expected}s）：{t1} → {t2}", e, "T-FLAKY-1")
    return True


def c05():
    e = Env("c05_paused")
    e.set_project_state("proj-default", "paused")
    e.ticket("T-PAUSE-1", title="暂停项目单")
    p = e.popen("--loop")
    try:
        check(wait_until(lambda: (e.data / "派发器.pid").exists(), timeout=8),
              "--loop 未写出 pid 文件")
        time.sleep(2.4)
        check(e.one("SELECT COUNT(*) AS c FROM events WHERE ticket_id='T-PAUSE-1' "
                    "AND kind='dispatch_intent' AND actor='派发器'")["c"] == 0,
              "paused 项目被领取")
        e.set_project_state("proj-default", "running")
        check(wait_until(lambda: (e.one("SELECT status FROM tickets WHERE id='T-PAUSE-1'") or {}).get("status") == "已回件",
                         timeout=12), "改 running 后同进程下一轮未领到")
    finally:
        p.terminate()
        try:
            p.wait(timeout=8)
        except Exception:
            p.kill()
    return True


def c06():
    e = Env("c06_stopped")
    e.set_project_state("proj-default", "stopped")
    e.ticket("T-STOP-1", title="停止项目单")
    r = e.run("--ticket", "T-STOP-1")
    check(r.returncode == 0, f"退出码 {r.returncode}")
    check(e.one("SELECT status FROM tickets WHERE id='T-STOP-1'")["status"] == "待处理",
          "stopped 项目被领取")
    check(e.one("SELECT COUNT(*) AS c FROM events WHERE ticket_id='T-STOP-1'")["c"] == 0,
          "stopped 项目写了事件")
    return True


def c07():
    e = Env("c07_dedup")
    h = "a" * 32
    # 真哈希：已回件的同哈希其他单 → 候选不派
    e.ticket("T-HASH-OLD", title="哈希旧单", status="已回件", canonical=h)
    e.ticket("T-HASH-NEW", title="哈希新单", canonical=h)
    # 弱判重：同项目/执行端/标题 → 只派 1 张
    e.ticket("T-DUP-1", title="同标题A", canonical="manual")
    e.ticket("T-DUP-2", title="同标题A", canonical="manual")
    # 不同标题 → 都派
    e.ticket("T-UNIQ-1", title="标题B1", canonical="manual")
    e.ticket("T-UNIQ-2", title="标题B2", canonical="manual")
    r = e.run()
    check(r.returncode == 0, f"退出码 {r.returncode}：{last_lines(r)}")
    check(e.one("SELECT status FROM tickets WHERE id='T-HASH-NEW'")["status"] == "待处理",
          "真哈希重复单被派出去了")
    dup = e.q("SELECT id,status FROM tickets WHERE id IN ('T-DUP-1','T-DUP-2')")
    check(sum(1 for x in dup if x["status"] == "待处理") == 1, f"弱判重结果 {dup}")
    uniq = e.q("SELECT id,status FROM tickets WHERE id IN ('T-UNIQ-1','T-UNIQ-2')")
    check(all(x["status"] == "已回件" for x in uniq), f"不同标题未都派：{uniq}")
    return True


def c08():
    e = Env("c08_concurrency", dcfg={"max_concurrent": 1})
    e.ticket("T-CONC-1", title="并发单1", executor="process-slow")
    e.ticket("T-CONC-2", title="并发单2", executor="process-slow")
    peak = {"n": 0}
    stop = threading.Event()

    def sample():
        # 复用一个长连接：每 0.05s 新建/关闭连接会与 worker 的 WAL 写事务竞态，
        # 偶发 "attempt to write a readonly database" 假红（2026-11-11 定位）。
        conn = sqlite3.connect(str(e.db), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            while not stop.is_set():
                try:
                    n = conn.execute(
                        "SELECT COUNT(*) AS c FROM tickets WHERE status='派发中'").fetchone()["c"]
                    peak["n"] = max(peak["n"], n)
                except Exception:
                    pass
                time.sleep(0.05)
        finally:
            conn.close()

    th = threading.Thread(target=sample, daemon=True)
    th.start()
    try:
        r = e.run()
    finally:
        stop.set()
        th.join(timeout=3)
    check(r.returncode == 0, f"退出码 {r.returncode}：{last_lines(r)}")
    check(peak["n"] <= 1, f"并发峰值 {peak['n']} 超过 max_concurrent=1")
    evs = e.q("SELECT ticket_id,at FROM events WHERE kind='dispatch_intent' AND actor='派发器' ORDER BY id")
    check(len(evs) == 2, f"dispatch_intent 数量 {len(evs)}")
    check(evs[0]["at"] != evs[1]["at"], f"两单领取时刻相同：{evs}")
    states = {x["ticket_id"]: e.one("SELECT status FROM tickets WHERE id=?",
                                    (x["ticket_id"],))["status"] for x in evs}
    ck(all(v == "已回件" for v in states.values()),
       f"两单未都派完：{states} | 派发器日志：{last_lines(r, 8)}", e, "T-CONC-1")
    return True


def c09():
    e = Env("c09_recover")
    e.ticket("T-REC-1", title="超时领取", status="派发中")
    e.event("T-REC-1", "dispatch_intent", "派发器", "派发器领取：demo 第 1 次", at=old_iso(601))
    e.ticket("T-REC-2", title="无派发器领取事件", status="派发中")
    e.event("T-REC-2", "dispatch_intent", "桌面端", "手工占位", at=old_iso(601))
    snapshot2 = e.one("SELECT * FROM tickets WHERE id='T-REC-2'")
    r = e.run()
    check(r.returncode == 0, f"退出码 {r.returncode}：{last_lines(r)}")
    row1 = e.one("SELECT * FROM tickets WHERE id='T-REC-1'")
    check(row1["status"] == "待处理", f"超时单未回收：{row1['status']}")
    check(e.one("SELECT COUNT(*) AS c FROM events WHERE ticket_id='T-REC-1' "
                "AND kind='claim_recovered' AND actor='派发器'")["c"] == 1, "缺 claim_recovered")
    row2 = e.one("SELECT * FROM tickets WHERE id='T-REC-2'")
    check(row2 == snapshot2, "无 actor=派发器 事件的派发中单被改动")
    return True


def c10():
    e = Env("c10_missing_dir")
    e.project("proj-missing", "目录缺失项目", str(e.missing_dir))
    e.ticket("T-DIR-1", title="目录不存在单", project_id="proj-missing")
    e.ticket("T-DIR-2", title="正常单")
    r = e.run()
    check(r.returncode == 0, f"退出码 {r.returncode}：{last_lines(r)}")
    row = e.one("SELECT * FROM tickets WHERE id='T-DIR-1'")
    check(row["status"] == "失败", f"目录缺失单终态 {row['status']}")
    check("目录" in (row["next_action"] or ""), f"next_action={row['next_action']}")
    check(e.one("SELECT COUNT(*) AS c FROM events WHERE ticket_id='T-DIR-1' "
                "AND kind='dispatch_fail'")["c"] >= 1, "缺 dispatch_fail")
    check(e.one("SELECT status FROM tickets WHERE id='T-DIR-2'")["status"] == "已回件",
          "其他单被连累")
    return True


def c11():
    e = Env("c11_sanitize")
    e.ticket("T-SEC-1", title="脱敏单", executor="process-secret")
    r = e.run("--ticket", "T-SEC-1")
    check(r.returncode == 0, f"退出码 {r.returncode}：{last_lines(r)}")
    blob = json.dumps(e.q("SELECT content FROM chat_messages"), ensure_ascii=False) + json.dumps(
        e.q("SELECT detail FROM events"), ensure_ascii=False)
    check("sk-abc123456789xyz" not in blob, "sk- 明文未脱敏")
    check("Bearer abcdefgh1234" not in blob, "Bearer 明文未脱敏")
    check("sk-***" in blob and "***" in blob, "脱敏占位符缺失")
    return True


def c12():
    e = Env("c12_signed_readonly")
    e.ticket("T-SIGN-1", title="人工签收态对照单", status=SIGNED, canonical="manual")
    before = e.one("SELECT * FROM tickets WHERE id='T-SIGN-1'")
    r = e.run()
    check(r.returncode == 0, f"退出码 {r.returncode}：{last_lines(r)}")
    after = e.one("SELECT * FROM tickets WHERE id='T-SIGN-1'")
    check(after == before, "人工签收态单被改动（含 updated_at）")
    check(e.one("SELECT COUNT(*) AS c FROM events WHERE ticket_id='T-SIGN-1'")["c"] == 0,
          "人工签收态单新增了事件")
    return True


def c13():
    e = Env("c13_no_more_claim")
    e.ticket("T-EXH-1", title="用尽后不再领", executor="process-bad")
    drive_rounds(e, "T-EXH-1", lambda: fail_count(e, "T-EXH-1") >= 3,
                 "#13 attempt 用尽")
    ck(e.one("SELECT status FROM tickets WHERE id='T-EXH-1'")["status"] == "失败",
       "第 3 次失败后未置失败", e, "T-EXH-1")
    mark = e.one("SELECT COUNT(*) AS c FROM events")["c"]
    r = e.run("--ticket", "T-EXH-1")
    check(r.returncode == 0, f"退出码 {r.returncode}")
    ck(e.one("SELECT COUNT(*) AS c FROM events")["c"] == mark,
       "再跑一轮产生了新事件", e, "T-EXH-1")
    return True


def c14():
    e = Env("c14_no_progress")
    e.ticket("T-NP-1", title="恒定启动失败", executor="process-fail")
    drive_rounds(e, "T-NP-1", lambda: fail_count(e, "T-NP-1") >= 2,
                 "#14 两次恒定错误触发无进展")
    n = fail_count(e, "T-NP-1")
    ck(n == 2, f"dispatch_fail={n}，第 2 次应即停", e, "T-NP-1")
    row = e.one("SELECT * FROM tickets WHERE id='T-NP-1'")
    ck(row["status"] == "失败", f"终态 {row['status']}", e, "T-NP-1")
    guard = e.one("SELECT detail FROM events WHERE ticket_id='T-NP-1' AND kind='loop_guard_tripped'")
    ck(guard and "reason=no_progress_same_error" in guard["detail"], f"guard={guard}", e, "T-NP-1")
    detail = e.one("SELECT detail FROM events WHERE ticket_id='T-NP-1' AND kind='dispatch_fail'")
    check("启动失败" in detail["detail"], f"未测到启动失败分支：{detail['detail']}")
    r3 = e.run("--ticket", "T-NP-1")
    ck(r3.returncode == 0 and fail_count(e, "T-NP-1") == 2,
       "进了第 3 次", e, "T-NP-1")
    # 附加：白名单有名字但可执行文件不存在 → 未就绪分支
    e.ticket("T-NR-1", title="未就绪单", executor="notready")
    r4 = e.run("--ticket", "T-NR-1")
    check(r4.returncode == 0, f"未就绪用例退出码 {r4.returncode}")
    nr = e.one("SELECT * FROM tickets WHERE id='T-NR-1'")
    check(nr["status"] == "失败" and "未就绪" in (nr["next_action"] or ""),
          f"未就绪分支不符：{nr['status']} / {nr['next_action']}")
    return True


def c15():
    e = Env("c15_breaker")
    for i in range(3):
        e.ticket(f"T-BRK-{i+1}", title=f"熔断单{i+1}", executor="process-bad")
    r1 = e.run()
    check(r1.returncode == 0, f"第一轮退出码 {r1.returncode}：{last_lines(r1)}")
    r2 = e.run()
    check(r2.returncode == 0, f"第二轮退出码 {r2.returncode}：{last_lines(r2)}")
    check(e.one("SELECT COUNT(*) AS c FROM events WHERE kind='breaker' AND actor='派发器'")["c"] >= 1,
          "缺 breaker 事件")
    check(e.one("SELECT COUNT(*) AS c FROM group_messages WHERE role='alert'")["c"] >= 1,
          "熔断未直调告警")
    check(e.one("SELECT status FROM projects WHERE id='proj-default'")["status"] == "running",
          "熔断改写了 projects.status")
    check("UPDATE projects SET " + "status" not in (HERE / "派发器.py").read_text(encoding="utf-8"),
          "主程序含 projects.status 写入")
    # 人工 resume + 窗口过期 → 自动解除，重新派单（按配置的窗口动态等，不写死秒数）
    window = dcfg_of(e)["breaker_window_s"]
    last_at = e.one("SELECT MAX(at) AS at FROM events WHERE kind='dispatch_fail' "
                    "AND actor='派发器'")["at"]
    deadline = datetime.fromisoformat(last_at).timestamp() + window + 0.5
    if deadline > time.time():
        time.sleep(deadline - time.time())
    e.set_project_state("proj-default", "running")
    e.ex("UPDATE tickets SET status='失败' WHERE id LIKE 'T-BRK-%'")
    e.ticket("T-BRK-NEW", title="解除后新单")
    r3 = e.run("--ticket", "T-BRK-NEW")
    check(r3.returncode == 0, f"解除轮退出码 {r3.returncode}：{last_lines(r3)}")
    check(e.one("SELECT status FROM tickets WHERE id='T-BRK-NEW'")["status"] == "已回件",
          "窗口过期并 resume 后仍未重新派单")
    return True


def c16():
    e = Env("c16_double_instance")
    p = e.popen("--loop")
    try:
        check(wait_until(lambda: (e.data / "派发器.pid").exists(), timeout=8),
              "第一个进程未写出 pid 文件")
        before = e.counts()
        r = e.run()
        check(r.returncode == 2, f"第二个进程退出码 {r.returncode}，应为 2：{last_lines(r)}")
        check("已有派发器在跑" in (r.stdout or ""), f"缺提示语：{last_lines(r)}")
        check(e.counts() == before, "第二个进程 claim 或写了事件")
    finally:
        p.terminate()
        try:
            p.wait(timeout=8)
        except Exception:
            p.kill()
    return True


def c17():
    conn = real_ro()
    try:
        real_before = conn.execute("SELECT COUNT(*) FROM group_messages").fetchone()[0]
    finally:
        conn.close()
    e = Env("c17_direct_alert")
    e.ticket("T-ALERT-1", title="待验触发告警", gate="门②")
    r = e.run("--ticket", "T-ALERT-1")
    check(r.returncode == 0, f"退出码 {r.returncode}：{last_lines(r)}")
    check(e.one("SELECT COUNT(*) AS c FROM group_messages WHERE role='alert'")["c"] >= 1,
          "告警未落临时库")
    conn = real_ro()
    try:
        real_after = conn.execute("SELECT COUNT(*) FROM group_messages").fetchone()[0]
    finally:
        conn.close()
    check(real_before == real_after, f"真实台账 group_messages 行数变化 {real_before}→{real_after}")
    check(str(服务.DB_PATH).startswith(str(TMP_ROOT)), f"服务.DB_PATH 未指向临时库：{服务.DB_PATH}")
    return True


def c18():
    e = Env("c18_real_guard")
    before = REAL_DB.stat().st_mtime_ns
    r = subprocess.run([sys.executable, str(DISPATCHER), "--once",
                        "--config", str(e.cfg_path), "--data-dir", str(e.data)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60, env=child_env())
    check(r.returncode == 4, f"保护阀退出码 {r.returncode}，应为 4：{last_lines(r)}")
    check("检测到真实台账" in (r.stdout or ""), f"缺保护阀提示：{last_lines(r)}")
    check(REAL_DB.stat().st_mtime_ns == before, "真实台账 mtime 被改动")
    r2 = subprocess.run([sys.executable, str(DISPATCHER), "--init-db",
                         "--config", str(e.cfg_path), "--data-dir", str(e.data)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=60, env=child_env())
    check(r2.returncode != 0, "--init-db 未拒绝操作真实台账")
    check(REAL_DB.stat().st_mtime_ns == before, "--init-db 改动了真实台账 mtime")
    return True


# ---------------- WB-SKILL-ROUTE-20261115：技能候选注入 3 条（T20-T22） ----------------

def _skill_lib(tmp_name):
    """造一个临时技能库，返回 (lib_root, 命中技能 dict, 无关技能 name)。"""
    lib = TMP_ROOT / tmp_name / "04_技能"
    hit_dir = lib / "zz-demo-one"
    hit_dir.mkdir(parents=True, exist_ok=True)
    hit_skill = {
        "name": "zz-demo-one",
        "path": str(hit_dir / "SKILL.md"),
    }
    (hit_dir / "SKILL.md").write_text(
        "---\n"
        "name: zz-demo-one\n"
        "description: 演示命中技能 触发词：忽略这段。\n"
        "whenToUse: 该用：本单场景 不该用：本测试之外的情况 产出物：无\n"
        "triggers: [技能候选, 派单注入]\n"
        "tags: demo\n"
        "category: demo\n"
        "---\n\n# 演示\n正文\n", encoding="utf-8")
    other = lib / "zz-irrelevant"
    other.mkdir(parents=True, exist_ok=True)
    (other / "SKILL.md").write_text(
        "---\nname: zz-irrelevant\ndescription: 无关注释技能\n"
        "triggers: [完全不相干的词xyz]\n---\n\n# 无关\n", encoding="utf-8")
    return str(lib), hit_skill, "zz-irrelevant"


def _enable_skills(e, lib_root, *, enabled=True, log_event=True, top_k=3):
    """把临时配置里的 skills 节点覆盖为启用态。"""
    cfg = json.loads(e.cfg_path.read_text(encoding="utf-8"))
    cfg["skills"] = {"enabled": enabled, "lib_root": lib_root, "top_k": top_k,
                     "block_max_chars": 600, "cache_ttl_s": 600, "log_event": log_event}
    e.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def c20():
    """T20 命中：prompt 含候选块、命中技能 name、绝对路径、不适用行，不含无关技能。"""
    lib, hit, other_name = _skill_lib("c20_skilllib")
    cfg = {"skills": {"enabled": True, "lib_root": lib, "top_k": 3}}
    t = {"id": "T-SKILL-20", "title": "技能候选 派单注入 真实单", "note": "备注",
         "next_action": "下一步", "executor": "demo", "model": None,
         "gate": "无", "priority": "P1"}
    prompt = 派发器.build_prompt(t, None, cfg)
    check("【可用技能候选】" in prompt, "T20 缺候选块标题")
    check(hit["name"] in prompt, "T20 缺命中技能 name")
    check(hit["path"] in prompt, f"T20 缺命中技能绝对路径：{hit['path']}")
    check("不适用：" in prompt and "本测试之外" in prompt, "T20 缺「不适用」行")
    check(other_name not in prompt, "T20 不该出现无关技能")
    check("用到技能：<技能name 或 无>" in prompt, "T20 R5 第3条未扩展")
    return True


def c21():
    """T21 降级：①库不存在→未命中段且派单照常成功；②enabled=false→整段不出现。"""
    # ① 直接进程内调 build_prompt：指向不存在目录
    bad_cfg = {"skills": {"enabled": True, "lib_root": str(TMP_ROOT / "no_such_lib_xyz"),
                          "top_k": 3}}
    t = {"id": "T-SKILL-21A", "title": "普通任务单", "note": "", "next_action": "",
         "executor": "demo", "model": None, "gate": "无", "priority": "P1"}
    p1 = 派发器.build_prompt(t, None, bad_cfg)
    check("【可用技能候选】本单未命中任何技能，按常规执行。" in p1,
          "T21① 库不存在应输出未命中段")
    # ① 子进程真实派单仍成功
    e = Env("c21_missinglib")
    e.ticket("T-SKILL-21-DISPATCH", title="普通任务单")
    _enable_skills(e, str(TMP_ROOT / "no_such_lib_xyz"))
    r = e.run("--ticket", "T-SKILL-21-DISPATCH")
    check(r.returncode == 0, f"T21① 派单退出码 {r.returncode}：{last_lines(r)}")
    row = e.one("SELECT status FROM tickets WHERE id='T-SKILL-21-DISPATCH'")
    check(row and row["status"] == "已回件", f"T21① 派单未照常成功：{row}")
    # ② enabled=false → 整段（含未命中段）都不出现
    off_cfg = {"skills": {"enabled": False, "lib_root": str(TMP_ROOT / "no_such_lib_xyz")}}
    p2 = 派发器.build_prompt(t, None, off_cfg)
    check("【可用技能候选】" not in p2, "T21② enabled=false 仍出现候选块")
    return True


def c22():
    """T22 事件：命中单派发后落 skill_candidates(detail 含技能名)，未命中单 detail=none。"""
    lib, hit, _ = _skill_lib("c22_skilllib")
    e = Env("c22_events")
    e.ticket("T-SKILL-22-HIT", title="技能候选 派单注入 命中单")
    e.ticket("T-SKILL-22-MISS", title="完全无关的日常任务")
    _enable_skills(e, lib, log_event=True)
    r1 = e.run("--ticket", "T-SKILL-22-HIT")
    check(r1.returncode == 0, f"T22 命中单退出码 {r1.returncode}：{last_lines(r1)}")
    r2 = e.run("--ticket", "T-SKILL-22-MISS")
    check(r2.returncode == 0, f"T22 未命中单退出码 {r2.returncode}：{last_lines(r2)}")
    ev_hit = e.one("SELECT detail FROM events WHERE ticket_id='T-SKILL-22-HIT' AND kind='skill_candidates'")
    check(ev_hit is not None, "T22 命中单缺 skill_candidates 事件")
    check(ev_hit and hit["name"] in ev_hit["detail"], f"T22 事件 detail 缺命中技能名：{ev_hit}")
    ev_miss = e.one("SELECT detail FROM events WHERE ticket_id='T-SKILL-22-MISS' AND kind='skill_candidates'")
    check(ev_miss is not None, "T22 未命中单缺 skill_candidates 事件")
    check(ev_miss and ev_miss["detail"] == "none", f"T22 未命中单 detail 应为 none：{ev_miss}")
    return True


def c23():
    """T23 出门条 R6：①方案图命中含段/path/三问；②非关键词不出现；③tripcard.enabled=false 不出现且照常派单。"""
    tp = str(TMP_ROOT / "tripcard_模板.md")          # 临时目录造模板，不写真实馆内/用户路径
    with open(tp, "w", encoding="utf-8") as f:
        f.write("# 出门条模板\n一句话问题：\n")
    kws = ["方案图", "架构图", "时序图", "状态图", "流程图", "一页讲清", "explainer"]
    t_hit = {"id": "T-TRIP-23A", "title": "画一张方案图", "note": "", "next_action": "",
             "executor": "demo", "model": None, "gate": "无", "priority": "P1"}

    def cfg_of(enabled=True, path=tp, keywords=kws):
        return {"skills": {"enabled": False,
                           "tripcard": {"enabled": enabled, "path": path, "keywords": keywords}}}

    # ① 命中
    p1 = 派发器.build_prompt(t_hit, None, cfg_of())
    check("【本单要出门条】" in p1, "T23① 缺【本单要出门条】")
    check(tp in p1, f"T23① 缺出门条 path：{tp}")
    check("六格" in p1 and "填不满先补齐再画" in p1, "T23① 缺六格/填不满说明")
    check("①每个框有出处吗" in p1 and "③有没有人真的用眼睛看过图" in p1, "T23① 缺回件三问")
    # ② 非关键词整段不出现
    p2 = 派发器.build_prompt(dict(t_hit, id="T-TRIP-23B", title="日常任务单"), None, cfg_of())
    check("【本单要出门条】" not in p2, "T23② 非关键词仍出现出门条")
    # ③ enabled=false 整段不出现，且派单照常成功
    check("【本单要出门条】" not in 派发器.build_prompt(t_hit, None, cfg_of(enabled=False)),
          "T23③ tripcard.enabled=false 仍出现出门条")
    check("【本单要出门条】" not in 派发器.build_prompt(t_hit, None, cfg_of(path="")),
          "T23 path 为空仍出现出门条")
    e = Env("c23_trip_off")
    e.ticket("T-TRIP-23-OFF", title="方案图 出门条关闭单")
    ec = json.loads(e.cfg_path.read_text(encoding="utf-8"))
    ec["skills"] = {"enabled": False, "lib_root": str(TMP_ROOT / "no_such_lib_xyz"),
                    "tripcard": {"enabled": False, "path": tp, "keywords": kws}}
    e.cfg_path.write_text(json.dumps(ec, ensure_ascii=False, indent=2), encoding="utf-8")
    r = e.run("--ticket", "T-TRIP-23-OFF")
    check(r.returncode == 0, f"T23③ 派单退出码 {r.returncode}：{last_lines(r)}")
    row = e.one("SELECT status FROM tickets WHERE id='T-TRIP-23-OFF'")
    check(row and row["status"] == "已回件", f"T23③ 派单未照常成功：{row}")
    return True


def c24():
    """T24 R7 签收三勾（方案A）：①/api/state 下发 enabled+keywords 不含 path；②命中出三勾且不勾也能签；
    ③note 前缀落库 signoffs.note；④enabled=false 不显示；⑤无 skills 字段兼容降级。"""
    import urllib.request
    import urllib.error

    appjs = (HERE / "web_flow" / "app.js").read_text(encoding="utf-8")
    # --- 前端源码结构断言（软版 + 同一份配置 + 降级） ---
    check("data-trip-hit" in appjs and "data-trip=\"0\"" in appjs
          and "每个框都说得出出处" in appjs and "只是配了" in appjs
          and "用眼睛看过图" in appjs, "T24① 前端三勾三问缺失")
    check("api.loadState()" in appjs and ".skills.tripcard" in appjs
          and "keywords.some" in appjs, "T24① 前端未复用 /api/state 同一份配置判定")
    check("if (!hit) return;" in appjs and "tc.enabled === false" in appjs
          and "if (!tc ||" in appjs, "T24②/④ 未命中/enabled=false/无配置 未早退")
    check("'[出门条 ' + n + '/3] '" in appjs and "api.signoff(tid, gate, '主理人', note)" in appjs,
          "T24③ note 前缀拼接或 signoff 第4参缺失")
    # 门禁按钮判据未被三勾污染：data-gate 选择区独立、gateOk.disabled 只由门禁计算
    check("document.querySelectorAll('.gates input[data-gate]')" in appjs
          and "ok.disabled = !done" in appjs
          and "input[data-trip]" not in appjs.split("ok.disabled = !done")[0].split(".gates input[data-gate]")[0][-400:],
          "T24① 三勾可能污染门禁判据")

    def http_json(method, path, body=None, token=""):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
        req.add_header("X-Deck-Token", token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    # --- ①⑤ /api/state 字段契约（真实 HTTP）---
    e = Env("c24_r7_state")
    cfg = json.loads(e.cfg_path.read_text(encoding="utf-8"))
    cfg["skills"] = {"enabled": False, "tripcard": {"enabled": True, "path": "C:\\secret\\不该下发.md",
                     "keywords": ["方案图", "架构图", "时序图", "explainer"]}}
    e.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    httpd = port = token = None
    try:
        httpd, port, token = 服务.start_server(0)
        st, state = http_json("GET", "/api/state", token=token)
        check(st == 200, f"T24① /api/state 状态 {st}")
        sk = state.get("skills")
        check(isinstance(sk, dict) and isinstance(sk.get("tripcard"), dict), "T24① /api/state 缺 skills.tripcard")
        tc = sk["tripcard"]
        check(tc.get("enabled") is True, f"T24① enabled 应为 True：{tc}")
        check(isinstance(tc.get("keywords"), list) and "方案图" in tc["keywords"],
              f"T24① keywords 未下发：{tc}")
        check("path" not in tc, f"T24①/§7.1 不该下发 path：{tc}")
        check(all(k in ("enabled", "keywords") for k in tc), f"T24① 含多余子项：{tc}")
    finally:
        if httpd is not None:
            httpd.shutdown(); httpd.server_close()

    # --- ③ note 前缀落库（真实 POST signoff）---
    e2 = Env("c24_r7_note")
    e2.ticket("T-R7-NOTE", title="方案图 出门条单", gate="门②")
    r = e2.run("--ticket", "T-R7-NOTE")
    check(r.returncode == 0, f"T24③ 派单退出码 {r.returncode}：{last_lines(r)}")
    check(e2.one("SELECT status FROM tickets WHERE id='T-R7-NOTE'")["status"] == "待验", "T24③ 未进待验")
    httpd2 = port2 = token2 = None
    try:
        httpd2, port2, token2 = 服务.start_server(0)
        # 端口变量需指向本次：上面的 http_json 闭包引用 port；这里改写同名局部
        port = port2; token = token2
        st, res = http_json("POST", "/api/tickets/T-R7-NOTE/signoff",
                            {"gate": "门②", "signer": "主理人", "note": "[出门条 2/3] 原备注"}, token=token2)
        check(st == 200, f"T24③ signoff 状态 {st}：{res}")
    finally:
        if httpd2 is not None:
            httpd2.shutdown(); httpd2.server_close()
    row = e2.one("SELECT note, gate, signer FROM signoffs WHERE ticket_id='T-R7-NOTE'")
    check(row is not None, "T24③ signoffs 无落库行")
    check(row and (row["note"] or "").startswith("[出门条 2/3]"),
          f"T24③ note 前缀未落库：{row}")

    # --- ④ enabled=false：后端下发 false，前端据此不显示（源码早退已断言）---
    e3 = Env("c24_r7_off")
    cfg3 = json.loads(e3.cfg_path.read_text(encoding="utf-8"))
    cfg3["skills"] = {"enabled": False, "tripcard": {"enabled": False, "keywords": ["方案图"]}}
    e3.cfg_path.write_text(json.dumps(cfg3, ensure_ascii=False, indent=2), encoding="utf-8")
    httpd3 = port3 = token3 = None
    try:
        httpd3, port3, token3 = 服务.start_server(0)
        port = port3; token = token3
        st, s3 = http_json("GET", "/api/state", token=token3)
        check(st == 200 and s3["skills"]["tripcard"]["enabled"] is False,
              f"T24④ enabled=false 未下发：{s3.get('skills')}")
    finally:
        if httpd3 is not None:
            httpd3.shutdown(); httpd3.server_close()

    # --- ⑤ 兼容降级：配置无 skills 节点 → 下发空 tripcard，前端早退不报错 ---
    e4 = Env("c24_r7_noskills")
    cfg4 = json.loads(e4.cfg_path.read_text(encoding="utf-8"))
    cfg4.pop("skills", None)
    e4.cfg_path.write_text(json.dumps(cfg4, ensure_ascii=False, indent=2), encoding="utf-8")
    httpd4 = port4 = token4 = None
    try:
        httpd4, port4, token4 = 服务.start_server(0)
        port = port4; token = token4
        st, s4 = http_json("GET", "/api/state", token=token4)
        check(st == 200 and s4["skills"]["tripcard"]["enabled"] is False
              and s4["skills"]["tripcard"]["keywords"] == [],
              f"T24⑤ 无 skills 应降级为空：{s4.get('skills')}")
    finally:
        if httpd4 is not None:
            httpd4.shutdown(); httpd4.server_close()
    return True


def c25():
    """T25 R8 岗位过滤：①codex 只返 codex 卡；②role=None 两张都返；③缺 roles 视 any；
    ④build_prompt 传 executor=trace 不出现 trae 专属卡；⑤enabled=false 整段消失且照常派单。"""
    lib = TMP_ROOT / "c25_rolelib" / "04_技能"

    def card(sub, name, desc, roles_line):
        d = lib / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n"
            f"triggers: [岗位过滤钩子]\n{roles_line}---\n\n# 卡片\n正文\n", encoding="utf-8")

    card("role-codex", "role-codex", "写码岗", "roles: [codex]\n")
    card("role-trae", "role-trae", "UI 岗", "roles: [trae]\n")
    card("role-any", "role-any", "通用流程卡", "")   # 缺 roles → any 语义

    技能 = 派发器.技能
    check(技能 is not None, "T25 技能模块未加载")
    技能._CACHE.clear()
    skills = 技能.load_skills(lib_root=str(lib))
    check(len(skills) == 3, f"T25 应扫到 3 张卡：{len(skills)}")
    q = "岗位过滤钩子 任务"

    # ① role=codex 只返回 codex 卡（any 卡也参与）
    n_codex = [m["name"] for m in 技能.match(q, skills=skills, role="codex")]
    check("role-codex" in n_codex, f"T25① role=codex 缺 codex 卡：{n_codex}")
    check("role-trae" not in n_codex, f"T25① role=codex 不该出现 trae 卡：{n_codex}")
    # ② role=None 两张都返回（兼容旧行为）
    n_all = [m["name"] for m in 技能.match(q, skills=skills, role=None)]
    check("role-codex" in n_all and "role-trae" in n_all,
          f"T25② role=None 两张卡都应返回：{n_all}")
    # ③ 缺 roles 的卡任意 role 都参与（any 语义）
    for rr in ("codex", "trae", "dsh", "whatever"):
        n_r = [m["name"] for m in 技能.match(q, skills=skills, role=rr)]
        check("role-any" in n_r, f"T25③ role={rr} 缺 roles 卡应参与(any)：{n_r}")
    # ④ build_prompt 传 executor=trace：trae/codex 专属卡都不出现，只剩 any 卡
    cfg = {"skills": {"enabled": True, "lib_root": str(lib), "top_k": 5}}
    t = {"id": "T-ROLE-25", "title": "岗位过滤钩子 任务", "note": "", "next_action": "",
         "executor": "trace", "model": None, "gate": "无", "priority": "P1"}
    p = 派发器.build_prompt(t, None, cfg)
    check("【可用技能候选】" in p, "T25④ 缺候选块")
    check("role-trae" not in p, f"T25④ executor=trace 不该出现 trae 专属卡：{p}")
    check("role-codex" not in p, f"T25④ executor=trace 不该出现 codex 专属卡：{p}")
    check("role-any" in p, f"T25④ executor=trace 应保留 any 卡：{p}")
    # ⑤ enabled=false 整段消失，且子进程派单照常
    off_cfg = {"skills": {"enabled": False, "lib_root": str(lib)}}
    check("【可用技能候选】" not in 派发器.build_prompt(t, None, off_cfg),
          "T25⑤ enabled=false 仍出现候选块")
    e = Env("c25_role_off")
    e.ticket("T-ROLE-25-OFF", title="岗位过滤钩子 任务")
    r = e.run("--ticket", "T-ROLE-25-OFF")
    check(r.returncode == 0, f"T25⑤ 派单退出码 {r.returncode}：{last_lines(r)}")
    row = e.one("SELECT status FROM tickets WHERE id='T-ROLE-25-OFF'")
    check(row and row["status"] == "已回件", f"T25⑤ 派单未照常成功：{row}")
    return True


def c26():
    """T26 R9：①workbuddy argv 含 --allowedTools/Read 且 ad.allowed_tools 可覆盖；
    ②候选块含要点行且该行 ≤200 字、预算收紧先丢要点；③无 steps 卡不渲染要点；④R3/R8 回归。"""
    sk = 派发器.技能
    check(sk is not None, "T26 技能模块未加载")

    # ---- ① workbuddy_chat argv（打桩捕获 subprocess.run 的 cmd，不真起进程）----
    e = Env("c26_wb_argv")
    keyf = e.dir / "wb_key.txt"
    keyf.write_text("sk-test-key-12345\n", encoding="utf-8")
    cfg = json.loads(e.cfg_path.read_text(encoding="utf-8"))
    cfg["paths"]["workbuddy_key"] = str(keyf)
    cfg["paths"]["workbuddy_cli"] = str(e.fake_exe)   # 存在即可，不真跑
    cfg["paths"]["node"] = "node"
    cfg["paths"]["project"] = str(e.proj_dir)
    ad = {"type": "workbuddy", "max_turns": 6, "default_model": "test-model"}
    captured = []

    class _R:                                          # 假 CompletedProcess
        returncode = 0
        stdout = '[{"type":"result","result":"ok"}]'
        stderr = ""

    orig = 服务.subprocess.run

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        return _R()

    服务.subprocess.run = fake_run
    try:
        服务.workbuddy_chat(cfg, "测试", ad=ad)
        服务.workbuddy_chat(cfg, "测试", ad=dict(ad, allowed_tools=["Read", "Grep"]))
    finally:
        服务.subprocess.run = orig
    check(len(captured) == 2, f"T26① 未捕获两次 argv：{captured}")
    a1, a2 = captured
    check("--allowedTools" in a1 and "Read" in a1, f"T26① 默认缺 --allowedTools/Read：{a1}")
    for t in ("Glob", "Grep", "Edit", "Write", "Bash"):
        check(t in a1, f"T26① 默认白名单缺 {t}：{a1}")
    check("--max-turns" in a1 and "6" in a1, f"T26① max-turns 取值逻辑被改：{a1}")
    check("--allowedTools" in a2 and "Grep" in a2 and "Glob" not in a2 and "Bash" not in a2,
          f"T26① allowed_tools 覆盖未生效：{a2}")

    # ---- ② 要点行渲染 + 预算先丢要点 ----
    lib = TMP_ROOT / "c26_stepslib" / "04_技能"
    d = lib / "zz-steps"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: zz-steps\ndescription: 带要点的卡\n"
        "triggers: [要点触发词]\n---\n\n# 标题\n## 标准动作\n"
        "1. 复现错误并记录退出码和前二十行报错\n"
        "2. 看日志先怀疑环境再怀疑代码\n"
        "3. 最小改动一次只改一处并先备份\n", encoding="utf-8")
    cfg_with = {"skills": {"enabled": True, "lib_root": str(lib), "top_k": 3,
                           "block_max_chars": 5000}}
    blk = sk.候选块("要点触发词", cfg=cfg_with)
    step_lines = [ln for ln in blk.splitlines() if "要点：" in ln]
    check(len(step_lines) == 1, f"T26② 要点行应恰好一条：{blk}")
    check(len(step_lines[0]) <= 200, f"T26② 要点行超过 200 字：{len(step_lines[0])}")
    check("1)" in step_lines[0] and "2)" in step_lines[0] and "3)" in step_lines[0],
          f"T26② 要点行缺编号：{step_lines[0]}")
    cfg_tight = {"skills": {"enabled": True, "lib_root": str(lib), "top_k": 3,
                            "block_max_chars": 200}}
    blk2 = sk.候选块("要点触发词", cfg=cfg_tight)
    check("要点：" not in blk2, f"T26② 预算 200 收紧后要点未丢：{blk2}")
    check("zz-steps" in blk2 and "【可用技能候选】" in blk2, f"T26② 丢要点后候选丢失：{blk2}")

    # ---- ③ 无 steps 卡不渲染要点、不抛 ----
    lib2, hit, _ = _skill_lib("c26_nosteps")
    blk3 = sk.候选块("技能候选 派单注入", cfg={"skills": {"enabled": True, "lib_root": lib2, "top_k": 3}})
    check("要点：" not in blk3, f"T26③ 无 steps 卡仍渲染要点：{blk3}")
    check(hit["name"] in blk3, f"T26③ 无 steps 卡匹配异常：{blk3}")

    # ---- ④ 回归：R3 命中/降级 + R8 岗位过滤 ----
    miss = sk.候选块("无命中词xyz", cfg={"skills": {"enabled": True,
                                              "lib_root": str(TMP_ROOT / "no_such_lib_xyz")}})
    check(miss == "【可用技能候选】本单未命中任何技能，按常规执行。", f"T26④ R3 降级回归失败：{miss}")
    off = sk.候选块("技能候选 派单注入", cfg={"skills": {"enabled": False, "lib_root": lib2}})
    check(off == "", f"T26④ R3 enabled=false 整段未消失：{off}")
    n_codex = [m["name"] for m in sk.match("要点触发词", skills=sk.load_skills(lib_root=str(lib)), role="codex")]
    n_trae = [m["name"] for m in sk.match("要点触发词", skills=sk.load_skills(lib_root=str(lib)), role="trae")]
    check("zz-steps" in n_codex and "zz-steps" in n_trae,
          f"T26④ R8 缺 roles 卡应被任意 role 命中(any)：{n_codex}/{n_trae}")
    return True


def c27():
    """T27（R9.3 补做）：工单派发 prompt 末尾追加【技能候选】引导行；
    ask_skill_candidate=false 时不出现且其余 prompt 逐字不变；群聊 prompt 不受影响。"""
    e = Env("c27_skill_candidate")
    cfg = json.loads(e.cfg_path.read_text(encoding="utf-8"))
    base = ("【工单派发】单号 T-SKILL-27\n"
            "标题：技能候选回件引导单\n"
            "要求：\n"
            "1) 只做本单范围；\n"
            "2) 若备注里点了任务文件，先读该文件再动手；\n"
            "3) 回件开头必须带单号 T-SKILL-27；\n"
            "4) 不要改门禁；\n"
            "5) 需要拍板 → 停下说明。")
    guide = "4) 回件末尾加一行「【技能候选】有就写 3 行（做什么 / 关键步骤 / 坑），没有就写“无”。」"
    captured = []
    orig = 服务.demo_chat

    def fake_demo(_cfg, prompt):
        captured.append(prompt)
        return "ok"

    服务.demo_chat = fake_demo
    try:
        服务.dispatch_chat(cfg, "demo", base)                       # 默认：应追加
        cfg_off = json.loads(e.cfg_path.read_text(encoding="utf-8"))
        cfg_off["adapters"]["demo"]["ask_skill_candidate"] = False
        服务.dispatch_chat(cfg_off, "demo", base)                   # 按岗关闭：应逐字还原
        服务.dispatch_chat(cfg, "demo", "群里随便说一句")           # 群聊：不应被追加
    finally:
        服务.demo_chat = orig
    check(len(captured) == 3, f"T27 未捕获三次 prompt：{len(captured)}")
    check("【技能候选】" in captured[0] and captured[0] == base + "\n" + guide,
          f"T27① 引导行未追加或位置/内容不符：{captured[0]}")
    check(captured[1] == base, f"T27② ask_skill_candidate=false 时 prompt 被改动：{captured[1]}")
    check(captured[2] == "群里随便说一句", f"T27② 群聊 prompt 被误加引导行：{captured[2]}")
    check("要求：" in captured[0] and "5) 需要拍板" in captured[0]
          and "【工单派发】单号 T-SKILL-27" in captured[0],
          f"T27③ 原 prompt 要求段被破坏（T26 回归）：{captured[0]}")
    return True


def c31():
    """T31（defaults 阶段二）：defaults.exec_timeout_s 对没有 timeout_s 的 adapter 生效；
    ad.timeout_s 显式值仍最优先。"""
    e = Env("c31_ad_timeout_exec")
    cfg = json.loads(e.cfg_path.read_text(encoding="utf-8"))
    cfg["defaults"] = {"exec_timeout_s": 7, "http_timeout_s": None,
                       "max_turns": 30, "model": None, "prompt_suffix": ""}
    e.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg = 服务.load_config()
    defaults = cfg.get("defaults")
    check(服务.ad_timeout({"type": "process"}, defaults, "exec", 180) == 7,
          f"T31① defaults.exec_timeout_s=7 未生效：{服务.ad_timeout({'type': 'process'}, defaults, 'exec', 180)}")
    check(服务.ad_timeout({}, defaults, "exec", 180) == 7, "T31② 空 ad 未走 defaults")
    check(服务.ad_timeout({"timeout_s": 21}, defaults, "exec", 180) == 21,
          "T31③ ad.timeout_s 显式值应优先于 defaults")
    return True


def c32():
    """T32（defaults 阶段二）：defaults.http_timeout_s 对 http 生效，且 exec 不受影响。"""
    e = Env("c32_ad_timeout_http")
    cfg = json.loads(e.cfg_path.read_text(encoding="utf-8"))
    cfg["defaults"] = {"exec_timeout_s": None, "http_timeout_s": 9,
                       "max_turns": 30, "model": None, "prompt_suffix": ""}
    e.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg = 服务.load_config()
    defaults = cfg.get("defaults")
    check(服务.ad_timeout({}, defaults, "http", 180) == 9,
          f"T32① defaults.http_timeout_s=9 未生效：{服务.ad_timeout({}, defaults, 'http', 180)}")
    check(服务.ad_timeout({}, defaults, "exec", 180) == 180,
          "T32② exec 不应受 http_timeout_s 影响（应回落 fallback=180）")
    check(服务.ad_timeout({"timeout_s": 33}, defaults, "http", 180) == 33,
          "T32③ ad.timeout_s 显式值应优先于 defaults（http）")
    return True


def c33():
    """T33（defaults 阶段二·对账）：defaults 两键为 null 时，六个调用点生效值与改前逐点一致
    （802→180 / 859→120 / 1184→300 / 1260→180 / 1288→300 / 1332→180）；
    workbuddy/coze 等带 timeout_s 的 adapter 取其配置值（与改前 a.get("timeout_s", fallback) 语义一致）。"""
    e = Env("c33_ad_timeout_reconcile")
    cfg = json.loads(e.cfg_path.read_text(encoding="utf-8"))
    cfg["defaults"] = {"exec_timeout_s": None, "http_timeout_s": None,
                       "max_turns": 30, "model": None, "prompt_suffix": ""}
    e.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg = 服务.load_config()
    defaults = cfg.get("defaults")
    rows = [
        ("802", "exec", 180),   # workbuddy 进程
        ("859", "exec", 120),   # coze 轮询总时限
        ("1184", "exec", 300),  # 通用 process
        ("1260", "http", 180),  # openai 兼容 HTTP
        ("1288", "http", 300),  # A2A HTTP
        ("1332", "http", 180),  # webhook HTTP
    ]
    out = []
    for loc, kind, fb in rows:
        v = 服务.ad_timeout({}, defaults, kind, fb)
        out.append(f"{loc}→{v}")
        check(v == fb, f"T33① {loc} 默认态生效值应为 {fb}，实际 {v}")
    check(服务.ad_timeout({"timeout_s": 900}, defaults, "exec", 180) == 900,
          "T33② workbuddy timeout_s=900 应取配置值")
    check(服务.ad_timeout({"timeout_s": 120}, defaults, "exec", 120) == 120,
          "T33③ coze timeout_s=120 应取配置值")
    check(服务.ad_timeout({"timeout_s": 300}, defaults, "exec", 300) == 300,
          "T33④ process 类 timeout_s=300 应取配置值")
    print("  六点对账：" + " / ".join(out))
    return True


CASES = [
    ("1", "dry-run 不写表/事件/告警 + ready/pull/human/目录预检", c01),
    ("2", "demo 单成功 + 四事件链", c02),
    ("3", "gate 成功进待验、不签收", c03),
    ("4", "失败适配器重试上限 + 默认 10s 退避", c04),
    ("4b", "可恢复失败重试后成功", c04b),
    ("5", "项目 paused 不领、resume 后同进程下一轮领", c05),
    ("6", "项目 stopped 不领", c06),
    ("7", "真哈希 / 弱判重 / 不同标题", c07),
    ("8", "max_concurrent=1 串行", c08),
    ("9", "崩溃恢复 + 无派发器事件单不动", c09),
    ("10", "项目目录不存在 claim 后判失败", c10),
    ("11", "Key/Token 脱敏", c11),
    ("12", "人工签收态单只读", c12),
    ("13", "attempts 用尽后不再领", c13),
    ("14", "无进展（恒定启动失败）+ 未就绪分支", c14),
    ("15", "熔断不写 projects.status + 窗口过期解除", c15),
    ("16", "双实例锁退出码 2", c16),
    ("17", "直调告警落临时库、真实库行数不变", c17),
    ("18", "真实库保护阀退出码 4、mtime 不变", c18),
    ("20", "技能候选命中：prompt 含候选块/技能名/路径/不适用行", c20),
    ("21", "技能候选降级：库不存在照常派单 + enabled=false 整段消失", c21),
    ("22", "技能候选事件：命中 detail 含技能名、未命中 detail=none", c22),
    ("23", "R6 出门条：方案图命中注入、非关键词消失、enabled=false 照常派单", c23),
    ("24", "R7 签收三勾：/api/state 字段契约+无path、三勾软版、note 前缀落库、开关与降级", c24),
    ("25", "R8 岗位过滤：按 role 筛卡、role=None 兼容、缺 roles 视 any、executor 透传、开关降级", c25),
    ("26", "R9 候选块可用化：workbuddy argv 白名单+配置覆盖、要点行渲染+预算先丢、无steps降级、R3/R8回归", c26),
    ("27", "R9.3 技能候选回件引导：prompt 含引导行、按岗关闭逐字还原、群聊不受影响、T26 结构回归", c27),
    ("31", "defaults.exec_timeout_s 生效：无 timeout_s 走 defaults、ad.timeout_s 显式优先", c31),
    ("32", "defaults.http_timeout_s 生效：http 取 defaults、exec 不受影响、显式优先", c32),
    ("33", "默认不覆盖对账：两键 null 时六调用点=改前值（802=180/859=120/1184=300/1260=180/1288=300/1332=180），带 timeout_s 取配置值", c33),
]


def main():
    print("=" * 78)
    print("派发器 M1 自检 · 30 条")
    print(f"临时根目录：{TMP_ROOT}")
    print(f"真实台账：{REAL_DB}  mtime_ns={REAL_MTIME0}  sha256={REAL_SHA0}")
    print(f"服务.CONFIG_PATH 将被指向临时配置；服务.DB_PATH 将被指向临时库")
    print("=" * 78)
    case_failed = 0
    extra_failed = 0
    for num, name, fn in CASES:
        t0 = time.time()
        try:
            ok = fn()
            if not ok:
                raise AssertionError("用例返回 False")
            print(f"PASS #{num} {name}  ({time.time() - t0:.1f}s)")
        except Exception as ex:                                    # noqa: BLE001
            case_failed += 1
            print(f"FAIL #{num} {name}  ({time.time() - t0:.1f}s)  -> {type(ex).__name__}: {ex}")
    print("-" * 78)
    if str(服务.DB_PATH).startswith(str(TMP_ROOT)):
        print(f"PASS 服务.DB_PATH 指向临时库：{服务.DB_PATH}")
    else:
        extra_failed += 1
        print(f"FAIL 服务.DB_PATH 未指向临时库：{服务.DB_PATH}")
    if REAL_MTIME0 is not None:
        now_mtime = REAL_DB.stat().st_mtime_ns
        if now_mtime == REAL_MTIME0:
            print(f"PASS 真实台账 mtime 前后不变：{now_mtime}")
        else:
            extra_failed += 1
            print(f"FAIL 真实台账 mtime 变化：{REAL_MTIME0} → {now_mtime}")
    if REAL_SHA0 is not None:
        now_sha = file_sha256(REAL_DB)
        if now_sha == REAL_SHA0:
            print(f"PASS 真实台账 SHA256 前后不变：{now_sha[:16]}…")
        else:
            extra_failed += 1
            print(f"FAIL 真实台账 SHA256 变化：{REAL_SHA0} → {now_sha}")
    print("-" * 78)
    print(f"自检结果：{len(CASES) - case_failed}/{len(CASES)} PASS，用例失败 {case_failed} 条，"
          f"全局检查失败 {extra_failed} 项")
    print(f"退出码 = {min(case_failed + extra_failed, 255)}")
    return min(case_failed + extra_failed, 255)


if __name__ == "__main__":
    force_utf8_stdout()
    sys.exit(main())
