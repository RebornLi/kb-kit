#!/usr/bin/env python3
# ============================================================
# graph.py —— 知识图谱可视化（FR-3.3.15）
#   利用 related_to / prerequisite / supersedes 关系字段
#   生成 Mermaid 图到 70-知识治理 Governance/_graph.md
#   kb graph → 输出 Mermaid 图
#   kb graph --domain 开发 → 按 domain 过滤
# ============================================================
import argparse, sys, os, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_common import ROOT_DEFAULT, iter_notes, load_note, extract_relations, domain_primary

GRAPH_OUTPUT = "70-知识治理 Governance/_graph.md"
MAX_NODES = 200  # 防止图过大


def collect_nodes(root, domain_filter=None, content_type_filter=None):
    """收集所有有关系的笔记节点。"""
    nodes = {}  # rel → {fm, relations, domain, content_type}
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        try:
            fm, text, body = load_note(p)
        except (OSError, UnicodeDecodeError):
            continue
        # 过滤
        if domain_filter:
            if domain_primary(fm.get("domain", "")) != domain_filter:
                continue
        if content_type_filter and fm.get("content_type") != content_type_filter:
            continue
        relations = extract_relations(fm)
        if not relations:
            continue  # 无关系不收录
        nodes[rel] = {
            "fm": fm,
            "relations": relations,
            "domain": fm.get("domain", "-"),
            "content_type": fm.get("content_type", ""),
            "title": fm.get("kb_summary", Path(rel).stem)[:30],
        }
    return nodes


def build_mermaid(nodes, max_nodes=MAX_NODES):
    """生成 Mermaid 图。"""
    lines = ["```mermaid", "graph LR"]
    # 节点
    node_ids = {}
    for i, (rel, info) in enumerate(sorted(nodes.items())):
        if i >= max_nodes:
            break
        node_id = f"N{i}"
        node_ids[rel] = node_id
        title = info["title"].replace('"', "'")
        ct = info["content_type"]
        shape = "[]" if ct == "policy" else "()" if ct == "decision" else "(())"
        lines.append(f'  {node_id}{shape[:1]}{shape[1:] if len(shape)>1 else ""}["{title}"]')

    # 边
    edge_styles = {
        "related_to": "---",
        "prerequisite": "-->",
        "supersedes": "==>",
        "superseded_by": "-.->",
    }
    edge_labels = {
        "related_to": "相关",
        "prerequisite": "前置",
        "supersedes": "取代",
        "superseded_by": "被取代",
    }
    for rel, info in nodes.items():
        if rel not in node_ids:
            continue
        src_id = node_ids[rel]
        for field, target in info["relations"].items():
            # 处理字符串/列表/字符串形式列表
            if isinstance(target, str):
                if target.startswith("[") and target.endswith("]"):
                    targets = [t.strip().strip('"\'') for t in target[1:-1].split(",") if t.strip()]
                else:
                    targets = [target]
            elif isinstance(target, list):
                targets = target
            else:
                continue
            for tgt in targets:
                if tgt in node_ids:
                    tgt_id = node_ids[tgt]
                    style = edge_styles.get(field, "---")
                    label = edge_labels.get(field, field)
                    lines.append(f'  {src_id} {style}|{label}| {tgt_id}')

    lines.append("```")
    return "\n".join(lines)


def generate_graph(root, domain_filter=None, content_type_filter=None):
    """生成知识图谱并写入文件。"""
    nodes = collect_nodes(root, domain_filter, content_type_filter)
    if not nodes:
        return 0, "无关系型笔记"

    mermaid = build_mermaid(nodes)

    # 写入文件
    out_path = Path(root) / GRAPH_OUTPUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = f"""# 知识图谱

> 自动生成 · {len(nodes)} 个节点 · 关系字段: related_to / prerequisite / supersedes / superseded_by

"""
    out_path.write_text(header + mermaid + "\n", encoding="utf-8")
    return len(nodes), str(out_path.relative_to(root))


def main():
    ap = argparse.ArgumentParser(description="知识图谱可视化")
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--domain", default=None, help="按 domain 过滤")
    ap.add_argument("--content-type", default=None, help="按 content_type 过滤")
    args = ap.parse_args()
    count, output = generate_graph(args.root, args.domain, args.content_type)
    if count == 0:
        print(f"ℹ️  {output}")
    else:
        print(f"✅ 知识图谱已生成: {count} 个节点 → {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
