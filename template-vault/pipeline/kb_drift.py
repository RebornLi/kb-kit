#!/usr/bin/env python3
# ============================================================
# kb_drift.py —— 自进化知识库漂移度量器（治理维度）
#   重读 .kb/promotions.jsonl，对每条晋升记录评估：
#     1) 内容漂移率：target_note 当前正文 vs 记录时原文指纹是否一致
#     2) 平均长度变化：相对原文的增长/缩减幅度
#     3) 出处保留率：target_note 是否仍保留 kb_source frontmatter（防"出处稀释"）
#   纯本地、零外部依赖；仅标准库。
#   用法:
#     python3 pipeline/kb_drift.py [--root R]
# ============================================================
import argparse, json, hashlib, datetime, os, math
from pathlib import Path

LEDGER_NAME = "promotions.jsonl"
DEFAULT_ALERT_PPTS = 15  # 突变告警阈值：相邻窗口 drift% 跳增超过该 pp 数即告警
DECAY_AGE_DAYS = 60      # decay 语义：知识距今超过该天数标“建议复核/归档”


def _split_body(text):
    """返回 frontmatter（两个 --- 分隔线）之后的正文部分。"""
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return parts[2].lstrip("\n")
    return text


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_frontmatter_keys(target_path):
    """读目标笔记 frontmatter 的顶层 key 集合（用于判断 kb_source 等是否存在）。"""
    try:
        text = Path(target_path).read_text(encoding="utf-8")
    except OSError:
        return None
    parts = text.split("---", 2)
    if len(parts) < 2 or not parts[1].strip():
        return set()
    keys = set()
    for line in parts[1].splitlines():
        if ":" in line:
            keys.add(line.split(":", 1)[0].strip())
    return keys


def _has_git(root):
    """非严格判断 root 是否可访问 git 历史。"""
    from subprocess import run, DEVNULL
    try:
        run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            stdout=DEVNULL, stderr=DEVNULL, timeout=5)
        return True
    except Exception:
        return False


def _window_key(ts_str, window):
    """把 ledger 的 ts（ISO8601）解析成时间窗口键。"""
    try:
        ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return "unknown"
    if window == "day":
        return ts.strftime("%Y-%m-%d")
    if window == "week":
        iso = ts.isocalendar()
        return "%04d-W%02d" % (iso[0], iso[1])
    if window == "month":
        return ts.strftime("%Y-%m")
    return ts.strftime("%Y-%m-%d")


def _parse_age(ts_str, now):
    """返回记录距今秒数；解析失败返回 None。"""
    try:
        ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return (now - ts).total_seconds()
    except (ValueError, AttributeError, TypeError):
        return None


def _human_age(seconds):
    """把秒数变成可读的时间跨度。"""
    if seconds is None:
        return "未知"
    d = seconds / 86400.0
    if d >= 1:
        return "%.1f 天前" % d
    h = seconds / 3600.0
    return "%.0f 小时前" % h


def _analyze_records(records, now, root, window):
    """对每条记录算漂移/长度/出处，返回按 target_note 索引的聚合结果，
    使时间序列与 decay 可各自复用同一份计算。"""
    per_rec = []
    for rec in records:
        target_rel = rec.get("target_note", "")
        target_path = root / target_rel if target_rel else Path()
        entry = {
            "rec": rec,
            "drift": False,
            "length_ratio": 0.0,
            "preserved": False,
            "window": _window_key(rec.get("ts", ""), window),
        }
        if not target_path.exists():
            per_rec.append(entry)
            continue
        try:
            text = target_path.read_text(encoding="utf-8")
        except OSError:
            per_rec.append(entry)
            continue
        current_sha = _sha(_split_body(text))
        if current_sha != rec.get("sha256_written", ""):
            entry["drift"] = True
        orig = rec.get("original_text", "")
        if orig:
            entry["length_ratio"] = (len(text) - len(orig)) / max(len(orig), 1)
        keys = _read_frontmatter_keys(target_path)
        if keys is not None and "kb_source" in keys:
            entry["preserved"] = True
        age_s = _parse_age(rec.get("ts", ""), now)
        entry["age_seconds"] = age_s
        per_rec.append(entry)
    return per_rec


def _print_summary(per_rec):
    """A 的总览输出（单点快照），与旧版本逐行一致。"""
    total = len(per_rec)
    if total == 0:
        print("📈 漂移度量 · ledger 为空")
        return
    drifted = sum(1 for p in per_rec if p["drift"])
    length_ratios = [p["length_ratio"] for p in per_rec]
    preserved = sum(1 for p in per_rec if p["preserved"])
    latest = per_rec[-1]["rec"]
    drifted_pct = (drifted / total * 100) if total else 0
    preserved_pct = (preserved / total * 100) if total else 0
    avg_delta_pct = (sum(length_ratios) / len(length_ratios) * 100) if length_ratios else 0.0
    delta_sign = "+" if avg_delta_pct >= 0 else ""
    print("📈 漂移度量 · 共 %d 条晋升记录" % total)
    print("· 内容被改写: %d 条 (%.1f%%)      ← 正文已偏离写入时快照" % (drifted, drifted_pct))
    print("· 平均长度变化: %s%.1f%%          ← 相对原文" % (delta_sign, avg_delta_pct))
    print("· 出处保留率: %.1f%%              ← 仍含 kb_source frontmatter" % preserved_pct)
    print("· 最新记录: %s @ %s" % (latest.get("target_note", "—"),
                                     latest.get("ts", "—")))


def _print_trend(per_rec, window, alert):
    """FR-B1/B3：按时间窗口的漂移趋势表 + 突变告警。"""
    from collections import defaultdict
    win_total = defaultdict(int)
    win_drift = defaultdict(int)
    win_length = defaultdict(list)
    win_preserved = defaultdict(int)
    for p in per_rec:
        w = p["window"]
        win_total[w] += 1
        if p["drift"]:
            win_drift[w] += 1
        if p["preserved"]:
            win_preserved[w] += 1
        win_length[w].append(p["length_ratio"])
    if not win_total:
        return
    # 按窗口排序
    ordered = sorted(win_total.keys())
    prev_drift_pct = None
    label = "日" if window == "day" else ("周" if window == "week" else "月")
    print("\n📊 漂移趋势（按 %s 窗口）" % label)
    print("%s  %s  %s  %s  %s" % ("窗口", "晋升", "drift%", "length%", "preserved%"))
    for w in ordered:
        t = win_total[w]
        dpct = (win_drift[w] / t * 100) if t else 0
        lpct = (sum(win_length[w]) / len(win_length[w]) * 100) if win_length[w] else 0.0
        ppct = (win_preserved[w] / t * 100) if t else 0
        line = "%s  %3d  %5.1f%%  %+.1f%%  %.1f%%" % (w, t, dpct, lpct, ppct)
        # FR-B3：突变告警
        if prev_drift_pct is not None and (dpct - prev_drift_pct) >= alert:
            line += "   ⚠️ 突变（较上窗口 +%1.fpp），建议人工复核" % (dpct - prev_drift_pct)
            prev_drift_pct = None  # 每窗口只告警一次，避免连续刷屏
        else:
            prev_drift_pct = dpct
        print(line)


def _print_decay(per_rec):
    """FR-B2：新鲜度 / decay 视图（距今多久，超期告警）。"""
    from collections import defaultdict
    now = datetime.datetime.now().astimezone()
    ages = []
    for p in per_rec:
        s = p["age_seconds"]
        if s is None:
            continue
        ages.append((p))
    ordered = sorted(ages, key=lambda x: x["age_seconds"] if x["age_seconds"] is not None else 0, reverse=True)
    if not ordered:
        print("\n🕰️ 新鲜度：无有效时间戳记录")
        return
    total_valid = len(ordered)
    age_days = [p["age_seconds"] / 86400.0 for p in ordered if p["age_seconds"] is not None]
    if age_days:
        avg_days = sum(age_days) / len(age_days)
        oldest = max(age_days)
    else:
        avg_days = oldest = None
    print("\n🕰️ 新鲜度（decay 视图，距今由久到新）")
    for p in ordered:
        rec = p["rec"]
        age_s = p["age_seconds"]
        if age_s is None:
            continue
        flag = "   ⚠️ 建议复核/归档（>60 天）" if age_s / 86400.0 >= DECAY_AGE_DAYS else ""
        print("· %s · 写入于 %s · 距今 %s%s" % (
            rec.get("target_note", "—"), rec.get("ts", "—"),
            _human_age(age_s), flag))
    print("· 平均知识年龄: %s天" % ("%.1f" % avg_days if avg_days is not None else "未知"))
    print("· 最旧记录距今: %s" % _human_age(oldest) if oldest is not None else "· 最旧记录距今: 未知")


def main():
    ap = argparse.ArgumentParser(description="自进化知识库漂移度量器")
    ap.add_argument("--root", default=os.environ.get("KB_ROOT") or "",
                    help="vault 根目录（默认环境变量 KB_ROOT 或当前目录）")
    ap.add_argument("--window", choices=["day", "week", "month"], default=None,
                    help="按时间窗口输出漂移趋势（默认 day；省略则仅输出单点总览）")
    ap.add_argument("--decay", action="store_true",
                    help="输出新鲜度/衰减视图（距今多久 + 超期告警）")
    ap.add_argument("--alert", type=float, default=DEFAULT_ALERT_PPTS,
                    help="突变告警阈值 pp（相邻窗口 drift%% 跳增超过该值，缺省 %d）".replace("%d", str(DEFAULT_ALERT_PPTS)))
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else Path.cwd()

    ledger_path = root / ".kb" / LEDGER_NAME
    if not ledger_path.exists():
        print("📈 漂移度量 · 暂无 ledger（%s 不存在，可忽略）" % LEDGER_NAME)
        return 0

    records = []
    try:
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    except (OSError, json.JSONDecodeError) as e:
        print("⚠️ 读取 ledger 失败: %s" % e)
        return 1

    per_rec = _analyze_records(records, datetime.datetime.now().astimezone(), root, args.window)

    # 任何时间序列模式都先打印总览，再输出对应视图
    _print_summary(per_rec)
    if args.window:
        _print_trend(per_rec, args.window, args.alert)
    if args.decay:
        _print_decay(per_rec)

    # 可选：git 历史可见时，补充“被改写记录有多少已进提交”
    if not args.window and not args.decay and _has_git(root):
        try:
            from subprocess import run
            res = run(["git", "-C", str(root), "log", "--oneline"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if res.returncode == 0:
                print("· 历史检查: 可访问 git 历史（可用 git blame 复核被改写记录）")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    import subprocess  # noqa: E402  （供 _has_git 用）
    raise SystemExit(main())
