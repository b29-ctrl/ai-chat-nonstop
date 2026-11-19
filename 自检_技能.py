#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能候选注入模块独立自检（WB-SKILL-ROUTE-20261115 §5.2）。

全部临时目录造数据，不读真实技能库、不碰真实台账。可重复跑。
退出码 = 失败断言数。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import 技能                                             # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS = 0
FAIL = 0
TMP = Path(tempfile.mkdtemp(prefix="skill_selftest_"))


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS " + msg)
    else:
        FAIL += 1
        print("  FAIL " + msg)


def mk_lib(name):
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_skill(lib, subdir, *, name=None, triggers=None, description="", when="", tags=None,
                category="", fm=True):
    d = lib / subdir
    d.mkdir(parents=True, exist_ok=True)
    if not fm:
        (d / "SKILL.md").write_text("# 没有 frontmatter\n正文\n", encoding="utf-8")
        return d / "SKILL.md"
    lines = ["---"]
    if name is not None:
        lines.append(f"name: {name}")
    if description:
        lines.append(f"description: {description}")
    if when:
        lines.append(f"whenToUse: {when}")
    if triggers is not None:
        lines.append("triggers: [" + ", ".join(triggers) + "]")
    if tags is not None:
        lines.append("tags: " + ", ".join(tags))
    if category:
        lines.append(f"category: {category}")
    lines += ["---", "", "# 标题", "正文"]
    (d / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return d / "SKILL.md"


print("=" * 72)
print(f"技能模块自检 · 临时根：{TMP}")
print("=" * 72)

# ---- 1. 正常解析 ----
print("[1] 正常解析 name/description/whenToUse/triggers/tags/category/path")
lib = mk_lib("normal")
write_skill(lib, "alpha", name="alpha-one",
            description="演示技能描述", when="该用：甲 不该用：乙 产出物：丙",
            triggers=["钩子甲", "钩子乙"], tags=["t1", "t2"], category="demo")
技能._CACHE.clear()
sk = 技能.load_skills(lib_root=str(lib))
check(len(sk) == 1, "扫描到 1 个技能")
s = sk[0]
check(s["name"] == "alpha-one", "name 正确")
check(s["description"] == "演示技能描述", "description 正确")
check(s["whenToUse"].startswith("该用："), "whenToUse 正确")
check(s["triggers"] == ["钩子甲", "钩子乙"], "triggers 正确")
check(s["tags"] == ["t1", "t2"], "tags 正确")
check(s["category"] == "demo", "category 正确")
check(s["path"].endswith("alpha\\SKILL.md") or s["path"].endswith("alpha/SKILL.md"), "path 正确")

# ---- 2. 缺 name 用目录名兜底 ----
print("[2] 缺 name 用目录名兜底")
lib2 = mk_lib("noname")
write_skill(lib2, "fallback-dir", triggers=["兜底钩子"], description="x")
技能._CACHE.clear()
sk2 = 技能.load_skills(lib_root=str(lib2))
check(len(sk2) == 1 and sk2[0]["name"] == "fallback-dir", "目录名兜底为 name")

# ---- 3. 中文 name 标记为不合规 ----
print("[3] 中文 name → name_valid=False")
lib3 = mk_lib("cnname")
write_skill(lib3, "cn-dir", name="中文技能名", triggers=["x"])
技能._CACHE.clear()
sk3 = 技能.load_skills(lib_root=str(lib3))
check(len(sk3) == 1 and sk3[0]["name_valid"] is False, "中文 name 不合规")

# ---- 4. frontmatter 缺失跳过 ----
print("[4] frontmatter 缺失 → 跳过不抛")
lib4 = mk_lib("nofm")
write_skill(lib4, "no-frontmatter", fm=False)
write_skill(lib4, "good-one", name="good-one", triggers=["y"])
技能._CACHE.clear()
sk4 = 技能.load_skills(lib_root=str(lib4))
check(len(sk4) == 1 and sk4[0]["name"] == "good-one", "坏文件跳过、好文件保留")

# ---- 5. triggers 优先于 description 正则 ----
print("[5] triggers 列表优先于 description 的「触发词：」正则")
lib5 = mk_lib("priority")
write_skill(lib5, "p", name="p-one", triggers=["正确词BBB"],
            description="描述 触发词：错误词AAA。后续")
技能._CACHE.clear()
sk5 = 技能.load_skills(lib_root=str(lib5))
check("正确词BBB" in sk5[0]["triggers"] and "错误词AAA" not in sk5[0]["triggers"],
      "triggers 优先，忽略 description 触发词")

# ---- 6. triggers 中英文逗号分隔 ----
print("[6] triggers 中英文逗号都能切")
lib6 = mk_lib("commas")
write_skill(lib6, "c", name="c-one", triggers=["技能候选", "派单注入", "自动关联"])
技能._CACHE.clear()
sk6 = 技能.load_skills(lib_root=str(lib6))
check(sk6[0]["triggers"] == ["技能候选", "派单注入", "自动关联"], "列表三项切分正确")
# 直接构造含中文逗号的原始行
d6 = lib6 / "c" / "SKILL.md"
d6.write_text("---\nname: c-one\ntriggers: [技能候选，派单注入, 自动关联]\n---\n", encoding="utf-8")
技能._CACHE.clear()
sk6b = 技能.load_skills(lib_root=str(lib6))
check(sk6b[0]["triggers"] == ["技能候选", "派单注入", "自动关联"], "中英文逗号混用切分正确")

# ---- 7. tags 参与匹配（无 triggers、无 description 触发词时回退） ----
print("[7] tags 兜底参与匹配")
lib7 = mk_lib("tags")
write_skill(lib7, "t", name="t-one", description="无触发词描述", tags=["标签钩子XYZ"])
技能._CACHE.clear()
sk7 = 技能.load_skills(lib_root=str(lib7))
check("标签钩子XYZ" in sk7[0]["triggers"], "tags 回退进 triggers")
m7 = 技能.match("这个单子是 标签钩子XYZ 相关", skills=sk7)
check(len(m7) == 1 and m7[0]["name"] == "t-one", "tags 钩子可命中")

# ---- 8. whenToUse 提取「不该用」段 ----
print("[8] whenToUse 提取「不该用」→「产出物」之间")
na = 技能._not_use_60("该用：甲 不该用：本测试之外或生产禁区 产出物：丙")
check("本测试之外" in na and "产出物" not in na, "不该用段提取正确且不含产出物")
check(技能._not_use_60("") == "", "无 whenToUse 返回空")

# ---- 9. top_k 截断 ----
print("[9] top_k 截断")
fake = [
    {"name": f"s{i}", "path": f"p{i}", "triggers": ["钩子"], "tags": [], "description": ""}
    for i in range(5)
]
m9 = 技能.match("钩子 钩子 钩子", skills=fake, top_k=2)
check(len(m9) == 2, "top_k=2 只返回 2 条")

# ---- 10. min_score 过滤 ----
print("[10] min_score 过滤")
m10a = 技能.match("钩子", skills=[fake[0]], min_score=1)
m10b = 技能.match("无关文本", skills=[fake[0]], min_score=1)
check(len(m10a) == 1, "命中得分≥1 保留")
check(len(m10b) == 0, "未命中得分=0 过滤")

# ---- 11. 缓存命中（第二次不重复读盘，打桩计数） ----
print("[11] 缓存命中：第二次不重复解析 SKILL.md")
lib11 = mk_lib("cache")
write_skill(lib11, "k", name="k-one", triggers=["缓存钩子"])
技能._CACHE.clear()
orig = 技能._parse_skill
counter = {"n": 0}


def counted(path):
    counter["n"] += 1
    return orig(path)


技能._parse_skill = counted
try:
    技能.load_skills(lib_root=str(lib11))   # 第一次：读盘
    n1 = counter["n"]
    技能.load_skills(lib_root=str(lib11))   # 第二次：命中缓存
    n2 = counter["n"]
finally:
    技能._parse_skill = orig
check(n1 == 1, f"第一次解析 1 次（实得 {n1}）")
check(n2 == n1, f"第二次未重复读盘（计数仍 {n2}）")

# ---- 12. JSON 快速路径与扫描一致 + 过期回退 ----
print("[12] JSON 快速路径与扫描一致；JSON 过期回退扫描")
lib12 = mk_lib("jsonfast")
p1 = write_skill(lib12, "ja", name="json-a", triggers=["甲钩子"], description="甲描述", when="该用：甲 不该用：乙 产出物：丙")
p2 = write_skill(lib12, "jb", name="json-b", triggers=["乙钩子"], description="乙描述")
gen_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
idx = {
    "generated_at": gen_str,
    "skills": [
        {"id": "json-a", "dir": "ja", "path": str(p1), "triggers": ["甲钩子"],
         "description": "甲描述", "when": "该用：甲 不该用：乙 产出物：丙",
         "tags": [], "category": "", "name_valid": True},
        {"id": "json-b", "dir": "jb", "path": str(p2), "triggers": ["乙钩子"],
         "description": "乙描述", "tags": [], "category": "", "name_valid": True},
    ],
}
(lib12 / "_技能索引.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
技能._CACHE.clear()
cfg12 = {"skills": {"lib_root": str(lib12)}}
fast = 技能.load_skills(cfg=cfg12)               # lib_root=None → 走 JSON
scan = 技能.load_skills(lib_root=str(lib12))     # 显式 lib_root → 走扫描
check(all(x["source"] == "json" for x in fast), "走 JSON 快速路径")
check(all(x["source"] == "scan" for x in scan), "走扫描路径")
check({x["name"] for x in fast} == {x["name"] for x in scan}, "两路径 name 集合一致")
check(sorted((x["name"], tuple(x["triggers"])) for x in fast) ==
      sorted((x["name"], tuple(x["triggers"])) for x in scan), "两路径 triggers 一致")
# 令 SKILL.md 比索引新 → 过期回退
future = time.time() + 86400 * 365
os.utime(p1, (future, future))
技能._CACHE.clear()
after = 技能.load_skills(cfg=cfg12)
check(all(x["source"] == "scan" for x in after), "JSON 过期后回退扫描")

# ---- 13. block_max_chars 截断（按条丢低分项，不切半条） ----
print("[13] block_max_chars 超限按条截断")
lib13 = mk_lib("blockcut")
for i, h in enumerate(["钩子甲", "钩子乙", "钩子丙"]):
    write_skill(lib13, f"b{i}", name=f"b-{i}", triggers=[h],
                description="描述" + "长" * 30,
                when="该用：x 不该用：y-zzz 产出物：z")
技能._CACHE.clear()
cfg_big = {"skills": {"lib_root": str(lib13), "block_max_chars": 100000, "top_k": 3}}
cfg_small = {"skills": {"lib_root": str(lib13), "block_max_chars": 200, "top_k": 3}}
q13 = "钩子甲 钩子乙 钩子丙"
block_big = 技能.候选块(q13, cfg=cfg_big)
block_small = 技能.候选块(q13, cfg=cfg_small)
n_big = block_big.count("\n- ")
n_small = block_small.count("\n- ")
check(n_big == 3, f"大上限保留 3 条（实得 {n_big}）")
check(0 < n_small < n_big, f"小上限按条减少（{n_small} < {n_big}）")
check("读法：" in block_small and "（命中说明" in block_small, "截断后保留完整结构不切半条")

# ---- 14. 异常目录不抛 ----
print("[14] 库目录不存在 / 无权限不抛异常")
check(技能.load_skills(lib_root=str(TMP / "不存在目录")) == [], "不存在目录返回空列表")
check(技能.match("任意", skills=[], top_k=3) == [], "空技能列表 match 返回空")
check(技能.候选块("任意", cfg={"skills": {"lib_root": str(TMP / "不存在目录")}})
      == "【可用技能候选】本单未命中任何技能，按常规执行。", "不可用降级为未命中段")
check(技能.候选块("任意", cfg={"skills": {"enabled": False}}) == "", "enabled=false 返回空串")

print("-" * 72)
print(f"自检结果：{PASS}/{PASS + FAIL} 断言通过，失败 {FAIL} 条")
print(f"退出码 = {min(FAIL, 255)}")
sys.exit(min(FAIL, 255))
