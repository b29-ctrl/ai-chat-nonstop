#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能候选注入模块（WB-SKILL-ROUTE-20261115）· 仅标准库。

职责：扫描技能库 SKILL.md（或走 _技能索引.json 快速路径）→ 按触发词给工单打分
→ 产出可直接拼进派发 prompt 的候选块。任一环节异常都降级为「未命中」，绝不抛给调用方
（硬规则：技能模块抛异常 = 本单失败）。

对外接口：
    load_skills(lib_root=None, cfg=None) -> list[dict]
    match(query, skills=None, top_k=3, min_score=1, role=None) -> list[dict]
    候选块(query, cfg=None, role=None) -> str

R8：role（执行端，小写）非空时，候选只保留 roles 含该 role 或含 "any" 的卡；
roles 字段缺失视为 any。role 为空 → 全部参与（兼容 R1-R7 行为）。

R9.2：解析技能时从正文抽编号列表前 3 条存 steps；候选块在「不适用」后渲染「要点：」行；
block_max_chars 超预算先整体丢要点、再丢低分候选；无 steps 不渲染该行（降级不报错）。
两路径（扫描 + JSON 快速路径）都解析 steps：JSON 索引无该字段，按 path 只读 SKILL.md 正文。
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

LOG = logging.getLogger("deck_skill")

# 与 R4 默认值一致；缺 key 逐项兜底
DEFAULT_SKILLS = {
    "enabled": True,
    "lib_root": r"F:\共享记忆库\04_技能",
    "top_k": 3,
    "block_max_chars": 600,
    "cache_ttl_s": 600,
    "log_event": True,
}
DEFAULT_LIB = DEFAULT_SKILLS["lib_root"]

# 进程内解析缓存：(path, mtime_ns) -> (expire_epoch, parsed_dict)
_CACHE: dict = {}
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LIST_RE = re.compile(r"^\[(.*)\]$", re.S)
_TRIGGER_IN_DESC_RE = re.compile(r"触发词[:：]\s*(.+?)(?:。|$)")
_NOT_USE_RE = re.compile(r"不该用[:：]\s*(.+?)(?:产出物[:：]|$)", re.S)
_STEP_RE = re.compile(r"^\s*\d+[.)、]\s*(.+)$")       # R9.2：1. / 1) / 1、 开头的编号列表项


def _steps(text, n=3, width=40):
    """R9.2：从正文抽编号列表前 n 条要点；每条去掉 ** 加粗、截 width 字；抽不到返回 []。"""
    out = []
    try:
        for line in (text or "").splitlines():
            m = _STEP_RE.match(line)
            if not m:
                continue
            item = re.sub(r"\*\*", "", m.group(1)).strip()
            item = re.split(r"[。；;]", item)[0].strip()   # 取该条第一句，最短可读
            item = item[:width].strip()
            if item:
                out.append(item)
            if len(out) >= n:
                break
    except Exception:                                  # noqa: BLE001
        return []
    return out


def _cfg(cfg):
    """把配置归一化为 skills 子字典（逐 key 兜底）。"""
    raw = {}
    if isinstance(cfg, dict):
        node = cfg.get("skills")
        if isinstance(node, dict):
            raw = node
        elif cfg.get("lib_root") or cfg.get("top_k") or cfg.get("triggers"):
            raw = cfg                      # 允许直接传 skills 子树，便于自检
    out = dict(DEFAULT_SKILLS)
    for k in DEFAULT_SKILLS:
        if k in raw and raw[k] is not None:
            out[k] = raw[k]
    return out


def _as_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("false", "0", "no", "off", "")


def _as_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _split_list(raw):
    """解析 key: [a, b, c] 或 key: a,b,c；兼容中英文逗号。"""
    if raw is None:
        return []
    s = str(raw).strip()
    m = _LIST_RE.match(s)
    if m:
        s = m.group(1)
    parts = re.split(r"[,，]", s)
    return [p.strip().strip("'\"") for p in parts if p and p.strip()]


def _frontmatter(text):
    """取首个 --- 与下一个 --- 之间的 key: value；不做完整 YAML。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None
    fm = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip()
    return fm


def _parse_skill(path: Path):
    """解析单个 SKILL.md；坏文件返回 None，不抛。"""
    try:
        st = path.stat()
        text = path.read_text(encoding="utf-8-sig")
        fm = _frontmatter(text)
        if fm is None:
            return None
        name = (fm.get("name") or "").strip() or path.parent.name
        triggers = _split_list(fm.get("triggers"))
        if not triggers:
            m = _TRIGGER_IN_DESC_RE.search(fm.get("description", "") or "")
            triggers = [t.strip() for t in re.split(r"[/、,，]", m.group(1))] if m else []
        if not triggers:
            triggers = _split_list(fm.get("tags"))
        # R9.2：正文（去掉 frontmatter）抽标准动作要点；抽不到为空列表（降级不渲染）
        body = text.split("---", 2)[-1] if text.count("---") >= 2 else text
        return {
            "name": name,
            "steps": _steps(body),
            "title": (fm.get("display-name") or fm.get("title") or name).strip(),
            "path": str(path),
            "triggers": [t for t in dict.fromkeys(triggers) if t],
            "tags": _split_list(fm.get("tags")),
            "roles": [r.lower() for r in _split_list(fm.get("roles"))],   # R8 岗位过滤；缺失=[] 视作 any
            "category": (fm.get("category") or "").strip(),
            "description": (fm.get("description") or "").strip(),
            "whenToUse": (fm.get("whenToUse") or "").strip(),
            "mtime": st.st_mtime,
            "mtime_ns": st.st_mtime_ns,
            "name_valid": bool(_NAME_RE.match(name)),
            "source": "scan",
        }
    except Exception as ex:                              # noqa: BLE001
        LOG.warning("解析 SKILL.md 失败，跳过：%s (%s)", path, ex)
        return None


def _json_fresh_and_load(lib: Path, lib_root):
    """JSON 快速路径：索引存在且 generated_at 晚于任一 SKILL.md mtime 时返回 skills；否则 None。"""
    idx = lib / "_技能索引.json"
    try:
        if not idx.is_file():
            return None
        data = json.loads(idx.read_text(encoding="utf-8-sig"))
        items = data.get("skills") or []
        if not items:
            return None
        gen = data.get("generated_at") or ""
        if gen:
            gen_ts = time.mktime(time.strptime(gen[:19], "%Y-%m-%dT%H:%M:%S"))
            for d in lib.iterdir():
                if not d.is_dir() or d.name.startswith("_"):
                    continue
                sk = d / "SKILL.md"
                if sk.is_file() and sk.stat().st_mtime > gen_ts + 1:
                    return None                          # 有更新文件，索引过期
        out = []
        for it in items:
            p = it.get("path") or str(lib / (it.get("dir") or "") / "SKILL.md")
            name = (it.get("id") or it.get("name") or Path(p).parent.name).strip()
            # R9.2：JSON 索引无 steps 字段，按 path 只读 SKILL.md 正文抽取（不写馆内文件）
            steps = []
            try:
                if Path(p).is_file():
                    body = Path(p).read_text(encoding="utf-8-sig")
                    steps = _steps(body.split("---", 2)[-1] if body.count("---") >= 2 else body)
            except Exception:                          # noqa: BLE001
                steps = []
            out.append({
                "name": name,
                "steps": steps,
                "title": (it.get("display_name") or it.get("display-name") or name).strip(),
                "path": p,
                "triggers": [str(t) for t in (it.get("triggers") or []) if str(t).strip()],
                "tags": [str(t) for t in (it.get("tags") or []) if str(t).strip()],
                "roles": [str(r).strip().lower() for r in (it.get("roles") or []) if str(r).strip()],  # R8
                "category": (it.get("category") or "").strip(),
                "description": (it.get("description") or "").strip(),
                "whenToUse": (it.get("when") or it.get("whenToUse") or "").strip(),
                "mtime": Path(p).stat().st_mtime if Path(p).exists() else 0.0,
                "mtime_ns": Path(p).stat().st_mtime_ns if Path(p).exists() else 0,
                "name_valid": bool(it.get("name_valid")) if "name_valid" in it else bool(_NAME_RE.match(name)),
                "source": "json",
            })
        return out
    except Exception as ex:                              # noqa: BLE001
        LOG.warning("JSON 索引快速路径失败，回退扫描：%s", ex)
        return None


def load_skills(lib_root=None, cfg=None):
    """扫描 <lib_root>/*/SKILL.md，返回技能字典列表。目录缺失/单文件坏 → 跳过，不抛。"""
    c = _cfg(cfg)
    root = str(lib_root or c["lib_root"] or DEFAULT_LIB)
    now = time.time()
    try:
        lib = Path(root)
        if not lib.is_dir():
            LOG.warning("技能库目录不存在：%s", root)
            return []
        # 快速路径（仅当未显式指定 lib_root 覆盖，避免自检临时库误读真实 JSON）
        if lib_root is None:
            fast = _json_fresh_and_load(lib, root)
            if fast is not None:
                return fast
        out = []
        for d in lib.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            sk = d / "SKILL.md"
            if not sk.is_file():
                continue
            key = (str(sk), sk.stat().st_mtime_ns)
            hit = _CACHE.get(key)
            if hit and hit[0] > now:
                out.append(hit[1])
                continue
            parsed = _parse_skill(sk)
            if parsed is not None:
                _CACHE[key] = (now + _as_int(c["cache_ttl_s"], 600), parsed)
                out.append(parsed)
        return out
    except Exception as ex:                              # noqa: BLE001
        LOG.warning("加载技能库异常，降级为空：%s", ex)
        return []


def _norm(s):
    return re.sub(r"[\s\W_]+", " ", str(s or "")).lower()


def match(query, skills=None, top_k=3, min_score=1, cfg=None, lib_root=None, role=None):
    """按触发词命中打分，返回降序 [{name, path, score, hit:[触发词...]}]。无语义召回。
    R8：role 非空时按 roles 过滤（含该 role 或含 any；roles 缺失视作 any）。"""
    try:
        skills = load_skills(lib_root=lib_root, cfg=cfg) if skills is None else skills
        if not skills:
            return []
        r = str(role).strip().lower() if role is not None else ""
        q = _norm(query)
        scored = []
        for s in skills:
            if r:                                          # R8 岗位过滤
                roles = [str(x).strip().lower() for x in (s.get("roles") or [])]
                if roles and r not in roles and "any" not in roles:
                    continue
            seen = set()
            score = 0
            hit = []
            for t in s.get("triggers") or []:
                tl = str(t).strip().lower()
                if not tl or tl in seen:
                    continue
                seen.add(tl)
                if tl in q:
                    score += max(1, len(str(t).strip()))
                    hit.append(str(t).strip())
            if score >= min_score:
                scored.append({"name": s["name"], "path": s["path"],
                               "score": score, "hit": hit, "_skill": s})
        scored.sort(key=lambda x: (-x["score"], x["name"]))
        for x in scored:
            x.pop("_skill", None)
        return scored[:max(0, top_k)]
    except Exception as ex:                              # noqa: BLE001
        LOG.warning("技能匹配异常，降级为空：%s", ex)
        return []


def _desc_80(s):
    d = re.sub(r"[（(]?全量表在 triggers 字段[）)]?", "", s or "")
    d = re.sub(r"触发词[:：].*$", "", d).strip()
    return d[:80]


def _not_use_60(when):
    m = _NOT_USE_RE.search(when or "")
    return m.group(1).strip()[:60] if m else ""


def 候选块(query, cfg=None, lib_root=None, role=None):
    """返回可拼进 prompt 的候选段；enabled=false 返回空串（整段不出现）；无命中返回未命中段。
    R8：role 透传给 match 做岗位过滤。"""
    c = _cfg(cfg)
    if not _as_bool(c["enabled"]):
        return ""
    miss = "【可用技能候选】本单未命中任何技能，按常规执行。"
    try:
        top_k = _as_int(c["top_k"], 3)
        limit = _as_int(c["block_max_chars"], 600)
        cands = match(query, top_k=top_k, cfg=cfg, lib_root=lib_root, role=role)
        if not cands:
            return miss
        by_name = {}
        try:
            for s in load_skills(lib_root=lib_root, cfg=cfg):
                by_name[s["name"]] = s
        except Exception:
            pass
        header = "【可用技能候选】（按本单标题/备注匹配，最多 3 条；相关才读）"
        entries = []                 # 每条为不可分割单元：base（含「不适用」）+ 可整体丢弃的 steps_line
        hits_all = []
        for cd in cands:
            s = by_name.get(cd["name"], {})
            base = f"- {cd['name']}：{_desc_80(s.get('description'))}（路径：{cd['path']}）"
            na = _not_use_60(s.get("whenToUse"))
            if na:
                base += f"\n  不适用：{na}"
            steps_line = ""
            steps = s.get("steps") or []                 # R9.2：无 steps 不渲染该行（降级）
            if steps:
                steps_line = "\n  要点：" + " ".join(f"{i+1}) {x}" for i, x in enumerate(steps))
            entries.append({"base": base, "steps_line": steps_line})
            hits_all.extend(cd["hit"])
        tail_lines = []
        if hits_all:
            tail_lines.append(f"（命中说明：命中触发词 {'、'.join(dict.fromkeys(hits_all))}）")
        tail_lines.append("读法：与你的任务相关就先读该 SKILL.md 再动手；不相关忽略，不要为了用技能而扩大范围。")
        tail = "\n".join(tail_lines)

        def render():
            return [e["base"] + e["steps_line"] for e in entries]

        # block_max_chars 预算：先整体丢「要点」行（不切半句），再丢低分候选；命中说明/读法保留，至少留 1 条
        while len("\n".join([header] + render() + [tail])) > limit:
            if any(e["steps_line"] for e in entries):
                for e in entries:
                    e["steps_line"] = ""
                continue
            if len(entries) > 1:
                entries.pop()
                continue
            break
        return "\n".join([header] + render() + [tail])
    except Exception as ex:                              # noqa: BLE001
        LOG.warning("候选块生成异常，降级未命中：%s", ex)
        return miss
