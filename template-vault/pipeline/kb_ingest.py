#!/usr/bin/env python3
# ============================================================
# kb_ingest.py —— RSI 的"真实外部摄入"入口（P0-2）
# ------------------------------------------------------------
# 职责：把【外部】素材永久存入不可变的 raw/ 层，作为 RSI 外部接地阀的
#       真值来源（external_inflow）。raw/ 是"外部世界 → 知识库"的唯一
#       受进口袋，且只进不出（引擎 T1/T2/T3 永不写回 raw/，见 kb_engine
#       的 _raw_guard 与 feedback_loop 的 is_raw_rel 守卫）。
#
# 输入（四种，互斥）：
#   --url <url>        抓取网页，转成精简 markdown 存 raw/
#   --file <path>      把本地文件(.md/.txt)剪藏进 raw/
#   --stdin            从标准输入读取 markdown/纯文本
#   --dir <dir>        批处理某目录下所有 .md/.txt
#
# 输出：raw/<domain>/<slug>.md，带 frontmatter _stamp：
#         kind=source / is_source=true / kb_source=<web|file|stdin>
#         url/domain/created/ingest_time/source_hash/source_format
#   写前 git_backup、写后 git commit checkpoint（可 reset --hard 回滚）。
#
# 幂等：按 source_hash 去重——已存在同样内容则跳过（不重复入库）。
#
# 用法：
#   python3 pipeline/kb_ingest.py --url "https://..." [--domain AI理论]
#   python3 pipeline/kb_ingest.py --file ./clip.md --domain 技术
#   cat note.md | python3 pipeline/kb_ingest.py --stdin --domain 综合
#   python3 pipeline/kb_ingest.py --dir ./inbox-clips --domain 资源
#   python3 pipeline/kb_ingest.py --url "https://..." --dry   # 只算不写
# ============================================================
import argparse, os, re, sys, html, subprocess
from pathlib import Path
from datetime import datetime

import kb_common
from kb_common import ROOT_DEFAULT, content_fingerprint, load_note

ROOT = str(Path(__file__).resolve().parent.parent)  # template-vault 根
RAW_TOP = "raw"
DEFAULT_DOMAIN = "外部"
SLUG_RE = re.compile(r"[^A-Za-z0-9\u4e00-\ufffd]+")  # 保留字母数字与中文，其余转 '-'
MAX_BODY = 200000  # 单源正文上限（字节），防一个网页撑爆 raw/


# ── 采集：把各类输入变成 (title, body_md, kb_source) ─────────
def fetch_url(url):
    """抓取网页，极简 HTML→markdown（去 script/style/nav，保留正文文本）。"""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "kb-kit-rsi-ingest/1.0"})
    raw = urllib.request.urlopen(req, timeout=30).read()
    text = raw.decode("utf-8", errors="replace")
    title = ""
    m = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
    if m:
        title = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()[:120]
    # 去噪音块
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<(head|footer|nav|header)[^>]*>.*?</\1>", " ", text)
    # 标签 → 换行/空格
    text = re.sub(r"(?s)<(p|div|br|li|h[1-6])[^>]*>?", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    body = re.sub(r"\n{3,}", "\n\n", text).strip()
    return (title or url)[:200], body


def read_file(path):
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() == ".md":
        # 若文件自身有 frontmatter，只保留正文作为"外部素材"
        _, _, body = load_note(p)
        body = body.strip() or text.strip()
        return p.stem.strip() or p.name, body
    return p.stem.strip()[:120], text.strip()


def read_stream(text):
    text = text.strip()
    lines = text.splitlines()
    title = ""
    for ln in lines:
        if ln.lower().startswith("#"):
            title = ln.lstrip("#").strip()[:120]
            break
    return title, text


# ── 写入 ────────────────────────────────────────────────────
def slugify(title, domain):
    """返回 {raw}/{domain>/<slug>.md（不含撞名后缀；去重后再处理撞名）。"""
    base = SLUG_RE.sub("-", (title or domain or "source")).strip("-").lower()
    base = base[:70] or "source"
    domain_dir = SLUG_RE.sub("-", domain).strip("-") or DEFAULT_DOMAIN
    return Path(RAW_TOP) / domain_dir / (base + ".md")


def stamp_frontmatter(kb_source, domain, url, title, body, source_format="md"):
    now = datetime.now().isoformat(timespec="seconds")
    lines = [
        f"title: {title}",
        "kind: source",
        "is_source: true",
        f"kb_source: {kb_source}",
        f"domain: {domain}",
        f"created: {datetime.now():%Y-%m-%d}",
        f"ingest_time: {now}",
        f"source_format: {source_format}",
        f"url: {url}" if url else "url: ",
        f"source_hash: {content_fingerprint(body)[:24]}",
    ]
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body + "\n"


def git_commit(root, rels, msg):
    subprocess.run(["git", "-C", root, "add", "--", *rels], capture_output=True)
    if subprocess.run(["git", "-C", root, "status", "--porcelain"],
                      capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", msg],
                       capture_output=True)


def ingest_one(root, target: Path, body: str, kb_source, domain, url, dry):
    body = body[:MAX_BODY].rstrip()          # 规范化尾随空白，指纹才稳定
    h = content_fingerprint(body)
    d = target.parent
    # 1) 内容级幂等：整个 domain 目录内查重，命中则跳过（先于撞名后缀之前）
    #    读回 body 带分隔符残留的两侧空白 → 用 .strip() 归一化再比
    if d.is_dir():
        for existing in d.glob("*.md"):
            try:
                _, _, b = load_note(existing)
                if content_fingerprint(b.strip()) == h:
                    return {"status": "duplicate", "path": existing, "hash": h[:12]}
            except (OSError, UnicodeDecodeError):
                continue
    # 2) 撞名则加后缀（此时 base 名确认可写）
    d.mkdir(parents=True, exist_ok=True)
    base_name = target.name
    cand = d / base_name
    n = 2
    while cand.exists():
        cand = d / (base_name[:-3] + "-" + str(n) + ".md")
        n += 1
    target = cand
    if not dry:
        subprocess.run(["git", "-C", root, "add", "-u"], capture_output=True)
        if subprocess.run(["git", "-C", root, "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip():
            subprocess.run(["git", "-C", root, "commit", "-q", "-m", "pre-ingest checkpoint"],
                           capture_output=True)
        target.write_text(stamp_frontmatter(kb_source, domain, url,
                                            target.stem, body), encoding="utf-8")
        git_commit(root, [str(target)], f"kb: ingest {kb_source} 源 → raw/")
    return {"status": "written" if not dry else "dry-run",
            "path": target, "hash": h[:12]}


# ── CLI ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="RSI 真实外部摄入 → raw/（不可变）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="抓取网页存入 raw/")
    g.add_argument("--file", help="剪藏本地文件(.md/.txt)到 raw/")
    g.add_argument("--stdin", action="store_true", help="从 stdin 读取存入 raw/")
    g.add_argument("--dir", help="批处理目录下的所有 .md/.txt")
    ap.add_argument("--domain", default=DEFAULT_DOMAIN, help="领域（默认 外部）")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--dry", action="store_true", help="只算不写")
    args = ap.parse_args()
    root = args.root

    results = []
    if args.url:
        try:
            title, body = fetch_url(args.url)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            print(f"❌ 抓取失败 {args.url}: {e}")
            sys.exit(1)
        results.append(ingest_one(root, slugify(title, args.domain), body,
                                  "web_page", args.domain, args.url, args.dry))
    elif args.file:
        title, body = read_file(args.file)
        results.append(ingest_one(root, slugify(title, args.domain), body,
                                  "file", args.domain, None, args.dry))
    elif args.stdin:
        data = sys.stdin.read()
        title, body = read_stream(data)
        results.append(ingest_one(root, slugify(title, args.domain), body,
                                  "stdin", args.domain, None, args.dry))
    elif args.dir:
        d = Path(args.dir)
        for f in sorted(d.glob("*.md")) + sorted(d.glob("*.txt")):
            title, body = read_file(f)
            results.append(ingest_one(root, slugify(title, args.domain), body,
                                      "file", args.domain, None, args.dry))

    ok = sum(1 for r in results if r["status"] in ("written", "dry-run"))
    dup = sum(1 for r in results if r["status"] == "duplicate")
    for r in results:
        print(f"{'📥 ' if r['status']=='written' else '👀 ' if r['status']=='dry-run' else '⏭️ '}"
              f"{r['status']:9} {r['path']}  (hash {r['hash']})")
    print(f"\n✅ 摄入 {ok} 篇 · 去重跳过 {dup} 篇 → {RAW_TOP}/"
          f"（--dry 模式未落盘）。raw/ 为不可变外部层，引擎永不写回。")


if __name__ == "__main__":
    main()
