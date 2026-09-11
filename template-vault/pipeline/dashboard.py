#!/usr/bin/env python3
# ============================================================
# dashboard.py —— 知识库健康仪表盘生成（方案 v4.0 §7.3）
#   输出: 70-知识治理/_INDEX.md  （含 ⚠️ 告警块，首页嵌入即可）
#   数据: validate.py 校验 / 备份新鲜度+sha256 / 管道同步与待审队列
# 用法:
#   python3 pipeline/dashboard.py [--root R] [--out 70-知识治理/_INDEX.md]
#   每周 cron 跑一次即可，或手动跑。
# ============================================================
import argparse, os, sys, json, subprocess, glob, datetime
from pathlib import Path

from kb_common import ROOT_DEFAULT

def run_validate(root):
    p = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "validate.py"),
                        "--root", root, "--json"], capture_output=True, text=True)
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {}

def check_backup(root, daily_dir="backups/daily"):
    zips = sorted(glob.glob(os.path.join(root, daily_dir, "*-vault.zip")), key=os.path.getmtime)
    if not zips:
        return {"ok": False, "msg": "无备份"}
    latest = zips[-1]
    sha = Path(latest).with_suffix(Path(latest).suffix + ".sha256")
    # 校验
    verified = "未知"
    if sha.exists():
        r = subprocess.run(["sha256sum", "-c", sha.name], cwd=os.path.dirname(latest),
                           capture_output=True, text=True)
        verified = "✓" if r.returncode == 0 else "✗"
    age_h = (datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(latest))).total_seconds() / 3600
    size_mb = os.path.getsize(latest) / 1024 / 1024
    age_str = f"{age_h:.1f}h" if age_h < 48 else f"{age_h/24:.1f}d"
    fresh = age_h < 30  # <30h 视为当日/近期
    return {"ok": True, "file": os.path.relpath(latest, root), "size_mb": round(size_mb, 2),
            "sha": verified, "age": age_str, "fresh": fresh, "n": len(zips)}

def check_pipeline(root):
    # 最近一次同步
    r = subprocess.run(["git", "-C", root, "log", "--oneline", "-1", "--grep=^kb: sync"],
                       capture_output=True, text=True)
    sync = r.stdout.strip().split("\n")[0] if r.stdout.strip() else "未同步"
    # 同步次数
    r2 = subprocess.run(["git", "-C", root, "log", "--oneline", "--grep=^kb: sync"],
                        capture_output=True, text=True)
    sync_n = len([l for l in r2.stdout.splitlines() if l.strip()])
    # 待审队列
    review = Path(root) / "70-知识治理" / "_review"
    rev_n = len([p for p in review.glob("*.md")] if review.exists() else [])
    return {"last_sync": sync or "无", "sync_n": sync_n, "review_queue": rev_n}


def check_growth(root):
    """调用成长度量引擎，返回结构化 dict；失败回空。"""
    ph = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "kb_health.py"),
                         "--json", "--root", root], capture_output=True, text=True)
    try:
        return json.loads(ph.stdout or "{}")
    except json.JSONDecodeError:
        return {}

def check_healthcheck(root):
    """调 kb-healthcheck.py 的 deadlinks + overlong，返回告警列表。
    dashboard 不含巡检结果（F-9），此处补全。"""
    alerts = []
    hc = str(Path(__file__).resolve().parent / "kb-healthcheck.py")
    for check in ("deadlinks", "overlong"):
        r = subprocess.run([sys.executable, hc, check, "--root", root],
                           capture_output=True, text=True)
        for line in (r.stdout or "").splitlines():
            # 解析 "🔗 死链检测: N/M 死链 (X%)" / "📏 超长未分块: N 个(...)"
            if "死链检测:" in line and "0 死链" not in line:
                alerts.append(f"🔗 {line.strip()}")
            elif "超长未分块:" in line and " 0 个" not in line:
                alerts.append(f"📏 {line.strip()}")
    return alerts

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    root = args.root

    rep = run_validate(root) or {}
    backup = check_backup(root)
    pipe = check_pipeline(root)
    growth = check_growth(root)
    hc_alerts = check_healthcheck(root)

    total = rep.get("total", 0)
    g_score = growth.get("score")
    g_label = growth.get("label", "")
    valid = rep.get("valid", 0)
    hard = rep.get("hard_issues", 0)
    warn = rep.get("warn_issues", 0)
    by_status = rep.get("by_status", {})
    by_domain = rep.get("by_domain", {})

    # 告警决策
    alerts = []
    if hard: alerts.append(f"⚠️ {hard} 条硬错误待治理（frontmatter/取值域）")
    if warn: alerts.append(f"⚠️ {warn} 条软告警（正文过长，§3.3 颗粒度）")
    if not backup["ok"]: alerts.append("⚠️ 无有效备份")
    elif not backup["fresh"]: alerts.append(f"⚠️ 备份已 {backup['age']} 未更新")
    elif backup["sha"] != "✓": alerts.append("⚠️ 备份 sha256 校验未通过")
    if pipe["review_queue"]: alerts.append(f"⚠️ 待审队列 {pipe['review_queue']} 条")
    if g_score is not None and g_score < 55:
        for a in growth.get("alerts", []):
            alerts.append(f"🌱 成长性：{a}")
    alerts.extend(hc_alerts)  # 巡检告警（死链/超长未分块）
    if not alerts: alerts.append("✅ 知识库运行正常")

    # 状态分布条
    bar = " · ".join(f"{k}:{v}" for k, v in sorted(by_status.items())) or "—"
    dom = " · ".join(f"{k}:{v}" for k, v in sorted(by_domain.items()) if k != "-") or "—"

    # 成长性详情（放在 more 之后，完整版可见；首页仅嵌入 cut 之前）
    def glist(items):
        return "\n".join(f"- `{i}`" for i in items) if items else "—"
    if g_score is not None:
        gc, gs, gf, gk, ge = (growth.get("connectivity", {}), growth.get("staleness", {}),
                              growth.get("freshness", {}), growth.get("intake", {}),
                              growth.get("emergence", {}))
        gc_status = "🟢" if gc.get("avg_outbound_links", 0) >= 2 and gc.get("orphan_pct", 100) < 5 \
            else ("🟡" if gc.get("avg_outbound_links", 0) >= 1 else "🔴")
        growth_section = f"""
## 🌱 成长性（六维度）

| 维度 | 指标 | 状态 |
|------|------|------|
| 🔗 连接度 | 平均出链 {gc.get('avg_outbound_links','—')} / 孤儿 {gc.get('orphan_notes','—')} ({gc.get('orphan_pct','—')}%) | {gc_status} |
| ⏰ 时效 | 过期 {gs.get('stale_notes','—')} ({gs.get('stale_pct','—')}%) | — |
| 🌱 新鲜 | 近30天新增 {gf.get('new_30d','—')} ({gf.get('new_pct','—')}%) | — |
| 📥 沉淀 | 收件箱积压 {gk.get('backlog','—')} 条 | — |
| 💡 涌现 | MOC/综合笔记 {ge.get('moc_count','—')} 条 | — |
| 🧩 去重 | {len(growth.get('dedup',{}).get('candidates',[]))} 对候选 | — |

### 治理建议

{chr(10).join('- '+a for a in growth.get('alerts', [])) or '✅ 无'}

### 孤儿笔记样例
{glist(gc.get('orphan_sample', []))}

### 过期待 review 样例
{glist([r for _, r in gs.get('stale_sample', [])])}

### 收件箱积压样例
{glist(gk.get('backlog_sample', []))}

### 去重合并候选
{glist([f'{a} ↔ {b}' for _, a, b in growth.get('dedup',{}).get('candidates', [])])}
"""
    else:
        growth_section = "\n## 🌱 成长性\n\n_度量引擎暂未返回数据_"

    dashboard = f"""# 📊 知识库管理仪表盘

> 生成时间 {datetime.datetime.now():%F %T} · 数据源: `pipeline/validate.py` + 备份校验 + git 审计
> 本文件由 `pipeline/dashboard.py` 生成，每周 cron 刷新一次。

## ⚠️ 运行告警

{chr(10).join(alerts)}

## 📈 概况

| 指标 | 数值 |
|------|------|
| 笔记总数 | **{total}** |
| 合格 | {valid} |
| 硬错误( ✗ ) | **{hard}** |
| 软告警( ⚠ ) | {warn} |
| 最近同步 | {pipe['last_sync'] or '无'} |
| 同步次数 | {pipe['sync_n']} |
| 待审队列 | {pipe['review_queue']} |
| 🌱 成长性 | **{g_score}** {g_label} |

## 💾 备份健康

- 最近备份: `{backup.get('file','—')}`
- 大小: {backup.get('size_mb','—')} MB · 距今: {backup.get('age','—')} · 校验和: **{backup.get('sha','—')}**
- 快照份数: {backup.get('n','0')}

<!-- more -->  ← 首页仅嵌入到此处（告警+概况+备份健康）
{growth_section}

## 🗂️ 状态分布

`{bar}`

## 🧭 领域分布

`{dom}`

## 🔗 治理入口

- [[01-SOP-新增入库]] · [[02-SOP-流转归档]] · [[03-SOP-健康巡检]] · [[04-质量与指标]] · [[命名约定与标签词库]]
- 恢复演练：见 scripts/drill_now.sh（方案 §2.3）

---
*仪表盘说明：健康/备份/管道三项指标见上；异常项出现在 ⚠️ 告警块中，按序治理。*
"""

    out = args.out or os.path.join(root, "70-知识治理 Governance", "_INDEX.md")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(dashboard, encoding="utf-8")
    print(f"✅ 仪表盘已生成: {out}")
    print(f"   告警: {' | '.join(alerts)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
