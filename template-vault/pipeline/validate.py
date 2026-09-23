#!/usr/bin/env python3
# ============================================================
# validate.py —— 知识库 frontmatter 只读校验（方案 v4.0 §3.2）
#   - 只读不写，被所有写入路径调用
#   - ✗ 硬错误（写入前强制）: 必填缺失 / 取值域非法 / kb_action 冲突
#   - ⚠ 软告警（颗粒度建议，非阻塞）: 正文过长 / retire 但 active
# 用法:
#   python3 pipeline/validate.py [--root VAULT] [--strict] [--json]
# ============================================================
import argparse, os, re, sys, json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from kb_common import ROOT_DEFAULT, EXCLUDE, DOMAIN_WHITELIST

ENUM_STATUS  = {"draft", "active", "stable", "legacy", "archived"}
REQUIRED = ["tags", "status", "domain", "created", "updated", "importance"]

# P2: 完整字段规范（FR-3.3.2）
OPTIONAL_SOURCE = ["author", "kb_source", "source_format", "source_hash", "ingest_time"]
OPTIONAL_CONTENT = ["content_type", "enforce_level", "chunk_of", "chunk_index"]
OPTIONAL_RELATION = ["related_to", "prerequisite", "supersedes", "superseded_by"]
OPTIONAL_CHAT = ["participants", "chat_time", "channel"]
OPTIONAL_GOVERNANCE = ["review_needed", "review_cycle", "last_reviewed"]
OPTIONAL_ALL = (OPTIONAL_SOURCE + OPTIONAL_CONTENT + OPTIONAL_RELATION +
                OPTIONAL_CHAT + OPTIONAL_GOVERNANCE)
BODY_LIMIT = 2000  # 字（§3.3 颗粒度建议上限）
# 引擎自生成的瞬时报表（已 gitignore）：由流水线产出不算源码笔记，不参与 frontmatter 校验
REPORTS = {"intake_triage.md", "feedback_hits.md", "link_suggestions.md",
           "recall_deck.md", "recall_schedule.md", "_INDEX.md",
           # 图谱自生成索引（无 frontmatter，不参与校验）
           "_graph.md", "_graph_kg.md"}

FM_RE = re.compile(r"^---\s*$", re.M)

# 注：parse_frontmatter 保留本地实现——与 kb_common.parse_frontmatter 签名/返回结构不同：
#   本地版返回 (fm_dict_or_None, line_number)，fm 值带 __line__/__value__ 元数据；
#   kb_common 版返回 (fm_dict, body_str)，fm 值为简单字符串。
#   validate 需行号定位错误 + 类型元数据，故保持独立。
def parse_frontmatter(text: str) -> Tuple[Optional[Dict[str, Any]], int]:
    """返回 (fm_dict_or_None, 起始行号)。无 frontmatter 返回 (None, 1)。"""
    m = FM_RE.search(text)
    if not m:
        return None, 1
    end = FM_RE.search(text, m.end())
    if not end:
        return None, m.start() + 1
    return parse_yaml(text[m.end():end.start()], m.start() + 1), m.start() + 1

# parse_yaml 无对应公共实现，保留本地版本。

def parse_yaml(block: str, start_line: int) -> Dict[str, Any]:
    fm, cur = {}, None
    for i, raw in enumerate(block.splitlines(), start=start_line):
        line = raw.strip()
        if not line or line.startswith("#") or line in ("{", "}"):
            continue
        if line.endswith(":"):                      # 键（无值）
            cur = line[:-1].strip().strip('"\'')
            fm[cur] = {"__missing__": True, "__line__": i, "__value__": None}
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key, val = key.strip().strip('"\''), val.strip()
            if val.startswith("[") and val.endswith("]"):
                items = [x.strip().strip('"\'') for x in val[1:-1].split(",") if x.strip()]
                fm[key] = {"__line__": i, "__value__": items}
            elif val in ("true", "false"):
                fm[key] = {"__line__": i, "__value__": val == "true"}
            else:
                fm[key] = {"__line__": i, "__value__": val.strip('"\'')}
    return fm

def is_missing(v: Any) -> bool:
    return isinstance(v, dict) and v.get("__missing__")

def validate_file(path: Union[str, Path]) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """返回 (hard_errors, warnings, info)。"""
    hard, warn, info = [], [], {"tags": [], "status": None, "domain": None, "importance": None}
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        return [f"读取失败: {e}"], [], info
    fm, line = parse_frontmatter(text)
    if fm is None:
        return [f"[{line}] 缺少 frontmatter"], [], info
    def g(k):
        v = fm.get(k)
        return None if is_missing(v) else (v["__value__"] if isinstance(v, dict) else v)
    for f in REQUIRED:
        v = fm.get(f)
        if v is None or is_missing(v):
            ln = v.get("__line__", 0) if isinstance(v, dict) else 0
            hard.append(f"[{ln}] 缺必填字段: {f}")
    st = g("status"); info["status"] = st
    if st is not None and st not in ENUM_STATUS:
        hard.append(f"status 非法: {st!r}（允许 {sorted(ENUM_STATUS)}）")
    dm = g("domain"); info["domain"] = dm
    if dm is not None and dm not in DOMAIN_WHITELIST:
        # P2: 两级 domain 校验（FR-3.3.3）
        try:
            from kb_common import is_valid_domain
            if not is_valid_domain(dm):
                hard.append(f"domain 非法: {dm!r}（不在 domain-taxonomy.json 白名单）")
            else:
                # 两级 domain 合法，降级为 warning（旧白名单不包含两级）
                warn.append(f"domain {dm!r} 为两级格式（P2 新增，旧白名单未收录）")
        except ImportError:
            hard.append(f"domain 非法: {dm!r}（允许 {sorted(DOMAIN_WHITELIST)}）")
    imp = g("importance"); info["importance"] = imp
    if imp is not None:
        try:
            fv = float(imp)
            if not (0.0 <= fv <= 1.0):
                hard.append(f"importance 越界: {imp}")
        except ValueError:
            hard.append(f"importance 非数字: {imp!r}")
    action = g("kb_action")
    if action == "retire":
        if "status: active" in text or "status: stable" in text:
            warn.append("kb_action=retire 但正文标注 active/stable，建议先降级")
    body = text.split("---", 2)[-1] if "---" in text else text
    if len(body.replace("\n", "")) > BODY_LIMIT:
        warn.append(f"正文过长: {len(body.replace(chr(10),''))} 字（建议 500-2000，§3.3 颗粒度）")
    tg = g("tags")
    if isinstance(tg, list):
        info["tags"] = tg
    # P2: 可选字段类型校验（FR-3.3.2）
    ct = g("content_type")
    if ct is not None:
        info["content_type"] = ct
    el = g("enforce_level")
    if el is not None and el not in ("must", "should", "may", "info"):
        warn.append(f"enforce_level 非标准值: {el!r}（建议 must/should/may/info）")
    rn = g("review_needed")
    if rn is not None:
        info["review_needed"] = rn
    rc = g("review_cycle")
    if rc is not None:
        try:
            int(rc)
        except (ValueError, TypeError):
            warn.append(f"review_cycle 非整数: {rc!r}")
    return hard, warn, info

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("VAULT_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--strict", action="store_true", help="有硬错误即返回非零退出码")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root, files, valid = args.root, [], 0
    by_status, by_domain = {}, {}
    issues = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in EXCLUDE]
        # 跳过嵌套 git 仓库（子模块/独立子仓），保留 root 本身
        if dp != root and os.path.exists(os.path.join(dp, ".git")):
            dns[:] = []
            continue
        for fn in fns:
            if not fn.endswith(".md"):
                continue
            if fn in REPORTS:                      # 引擎自生成的报表，不参与校验
                continue
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, root)
            hard, warn, info = validate_file(full)
            files.append(rel)
            if hard or warn:
                issues.append({"file": rel, "hard": hard, "warn": warn})
            if not hard and not warn:
                valid += 1
            st = info["status"] or "无frontmatter"; by_status[st] = by_status.get(st, 0) + 1
            dm = info["domain"] or "-"; by_domain[dm] = by_domain.get(dm, 0) + 1

    if not args.json:
        n_hard = sum(1 for it in issues if it["hard"])
        warn_total = sum(len(it["warn"]) for it in issues)
        print(f"📚 知识库校验报告  root={root}")
        print(f"   笔记总数 {len(files)}  ✓合格 {valid}  ✗硬错误 {n_hard}  ⚠软告警 {warn_total}")
        print(f"   状态分布: {by_status}")
        print(f"   领域分布: {by_domain}")
        for it in sorted(issues, key=lambda x: x["file"]):
            print(f"\n  ✗ {it['file']}")
            for e in it["hard"]:
                print(f"      - {e}")
            for w in it["warn"]:
                print(f"      ⚠ {w}")
        print(f"\n  {n_hard} 条笔记有硬错误需治理；{warn_total} 条软告警供参考")
        print(f"  退出码: {'1 (strict+硬错误)' if (args.strict and n_hard) else '0'}")
    if args.json:
        print(json.dumps({"root": root, "total": len(files), "valid": valid,
                          "hard_issues": sum(1 for it in issues if it["hard"]),
                          "warn_issues": sum(1 for it in issues if it["warn"]),
                          "by_status": by_status, "by_domain": by_domain, "issues": issues},
                         ensure_ascii=False, indent=2))
    return 1 if (args.strict and any(it["hard"] for it in issues)) else 0

if __name__ == "__main__":
    sys.exit(main())
