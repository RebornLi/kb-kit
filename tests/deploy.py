#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deploy.py — 把开发仓的运行时代码升级到某个 kb-kit 库（dev-repo 专用）。

## 为什么不是简单的 `cp -r`

1. **只该动代码，绝不动内容**。开发仓的 `template-vault/*.md` 是模板内容，
   生产库里的同名文件是**用户自己的笔记** —— 覆盖就是删用户数据。
   本工具只复制 `pipeline/` 与 `scripts/`，并显式列出被跳过的内容文件。
2. **索引格式会变**。生产库可能是旧索引（如 v4 的 `df_idf.json`），升级后需重建
   （新代码仍能读旧索引，但拿不到性能）。重建属于必要步骤，不能忘。
3. **状态文件位置变了**（第十六轮统一到 `.kb/state/`）。旧位置仍可读，但要确认。
4. **必须可验证、可回滚**。升级后跑一套验证；回滚靠 git + 升级前备份。

## 用法

    python3 tests/deploy.py plan   --to /path/to/vault     # 只读：列出将变更的文件
    python3 tests/deploy.py rehearse                        # 在副本上完整演练一遍
    python3 tests/deploy.py apply  --to /path/to/vault      # 备份 → 复制 → 重建索引 → 验证

`apply` 需要显式 `--to`，且会在动手前自动备份；失败会打印回滚命令。
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "template-vault"
TESTENV = REPO / ".testenv"
PY = sys.executable

# 只部署**代码**：这两个目录是运行时；其余（*.md 内容、reference/ 配置）不动。
CODE_DIRS = ("pipeline", "scripts")
# 明确排除的非代码（防止将来有人往这两个目录里放内容文件被误覆盖）
SKIP_SUFFIX = (".md",)

# ── P2-6：契约漂移检测 ────────────────────────────────────────────
#
# 问题（2026-10-22 审计）：本工具只复制 `pipeline/` 与 `scripts/`，
# **完全不碰 `reference/`**。但代码改动经常要求契约同步升级，例如：
#   · `kb-schema.json` 新增/废弃字段（本轮 `conflict_with` → `conflicts_with`）
#   · `tag-taxonomy.json` 新增受控标签
#   · `triage-config.json` 调整阈值权重
#   · `content-type-signals.json` 增加信号词
# 部署后目标库仍跑**旧契约**，于是新代码读不到新字段/新词表，行为悄悄退化，
# 而 deploy 报告「升级成功」。
#
# 这里不自动覆盖 `reference/`（那可能冲掉用户的本地定制），而是**检测并报告**
# 哪些契约文件与开发仓不一致，让操作者显式决定。
CONTRACT_DIR = "reference"
# 这些是「契约」：改了会影响运行时行为，必须提示
CONTRACT_FILES = (
    "kb-schema.json", "tag-taxonomy.json", "taxonomy.json",
    "triage-config.json", "content-type-signals.json",
    "dedup-config.json", "domain-taxonomy.json", "synonyms.json",
)
# 这些是「用户私有配置」，**绝不覆盖也绝不提示**（api key、用户表）
PRIVATE_FILES = ("users.json", "plugin-config.json", "model.json")


def contract_drift(target: Path) -> List[Tuple[str, str, str]]:
    """比较目标库与模板的 `reference/` 契约文件哈希。

    Returns: [(文件名, 目标哈希, 模板哈希), ...]，只列出**不一致或缺失**的。
    目标缺失也列出（哈希为空串）：缺契约 = 新代码找不到字段定义。
    """
    out: List[Tuple[str, str, str]] = []
    for name in CONTRACT_FILES:
        a = target / CONTRACT_DIR / name
        b = TEMPLATE / CONTRACT_DIR / name
        ha = _sha(a) if a.exists() else ""
        hb = _sha(b) if b.exists() else ""
        if ha != hb:
            out.append((name, ha, hb))
    return out


def _contract_warning(drift: List[Tuple[str, str, str]]) -> str:
    """把契约漂移压成一行摘要（供 plan / apply 提示）。"""
    if not drift:
        return ""
    names = ", ".join(n for n, _a, _b in drift[:6])
    more = f" 等 {len(drift)} 个" if len(drift) > 6 else ""
    return (f"⚠️ 契约漂移：{names}{more} 与开发仓不一致 —— "
            f"新代码可能读不到新字段/新词表（reference/ 不由本工具部署）")


def _sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _is_code(p: Path) -> bool:
    """是否属于「该部署的代码」。

    排除：内容文件（.md）、隐藏文件（`.kb_rsi_proposals.jsonl` 之类运行态）、
    状态文件（.json/.jsonl）—— 它们要么是用户数据，要么是运行时产物。
    """
    if p.name.endswith(SKIP_SUFFIX) or p.name.startswith("."):
        return False
    if p.suffix in (".json", ".jsonl"):
        return False
    return True


def diff_code(target: Path) -> Tuple[List[str], List[str], List[str]]:
    """对比 dev 模板与目标库的代码目录。返回 (新增, 变更, 仅目标有)。"""
    added, changed, only_target = [], [], []
    for d in CODE_DIRS:
        src_dir, dst_dir = TEMPLATE / d, target / d
        if not src_dir.is_dir():
            continue
        srcs = {p.relative_to(src_dir): p for p in src_dir.rglob("*")
                if p.is_file() and _is_code(p) and "__pycache__" not in p.parts}
        dsts = {p.relative_to(dst_dir): p for p in dst_dir.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts} if dst_dir.is_dir() else {}
        for rel, sp in sorted(srcs.items()):
            dp = dsts.get(rel)
            key = f"{d}/{rel}"
            if dp is None:
                added.append(key)
            elif _sha(sp) != _sha(dp):
                changed.append(key)
        for rel in sorted(dsts):
            if rel not in srcs and _is_code(dsts[rel]):
                only_target.append(f"{d}/{rel}")
    return added, changed, only_target


def _index_state(vault: Path) -> Dict[str, str]:
    """读索引版本与文件名（不改动）。"""
    d = vault / "vector index"
    out = {"file": "无", "version": "无"}
    for name in ("df_idf.pkl", "df_idf.json"):
        p = d / name
        if p.exists():
            out["file"] = f"{name}（{p.stat().st_size / 1048576:.1f} MB）"
            break
    try:
        payload, ver = _load_vault_index(vault)
        out["version"] = str(ver)
        if payload:
            out["struct"] = "postings（v7 ✓）" if "postings" in payload else "旧格式（无 postings）"
    except Exception as e:      # noqa: BLE001  只读探测，失败不致命
        out["version"] = f"（读取失败：{type(e).__name__}）"
    return out


def _load_vault_index(vault: Path):
    """用**目标库自己的** rag.py 读它的索引。

    必须临时把 `<vault>/pipeline` 放进 sys.path —— 否则 exec_module 时
    rag.py 的 `from rag_index import ...` 会 ModuleNotFoundError（实测踩过）。
    """
    import importlib.util
    pdir = str(vault / "pipeline")
    sys.path.insert(0, pdir)
    try:
        for name in ("rag", "rag_index", "rag_score", "rag_query",
                     "kb_io", "kb_text", "kb_metautil",
                     "kb_config", "kb_domain", "kb_infer",
                     "state_manager"):
            sys.modules.pop(name, None)      # 别拿到别的库的同名模块
        spec = importlib.util.spec_from_file_location("_probe_rag", vault / "pipeline" / "rag.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.load_index(vault / "vector index")
    finally:
        try:
            sys.path.remove(pdir)
        except ValueError:
            pass


def cmd_plan(args) -> int:
    target = Path(args.to).resolve()
    if not (target / "pipeline").is_dir():
        print(f"✗ {target} 不是 kb-kit 库（缺 pipeline/）")
        return 2
    added, changed, only_target = diff_code(target)
    print(f"📋 升级计划　{TEMPLATE} → {target}\n")
    print(f"  新增 {len(added)} · 变更 {len(changed)} · 仅目标有（保留不动） {len(only_target)}")
    for label, items in (("新增", added), ("变更", changed)):
        if items:
            print(f"\n  [{label}]")
            for k in items[:60]:
                print(f"    {k}")
            if len(items) > 60:
                print(f"    … 共 {len(items)} 个")
    if only_target:
        print("\n  [仅目标库有，升级不会删除]")
        for k in only_target[:10]:
            print(f"    {k}")
    print("\n  ⚠️ 不会复制的内容文件（模板内容 ≠ 你的笔记）：")
    for md in sorted(TEMPLATE.glob("*.md")):
        if (target / md.name).exists():
            print(f"    {md.name}")
    print("\n  索引状态：")
    src_idx = {"file": "（dev 尚未建索引）", "version": "-"}
    for k, v in _index_state(target).items():
        print(f"    目标库 {k}: {v}")
    try:
        sys.path.insert(0, str(TEMPLATE / "pipeline"))
        for m in list(sys.modules):
            if m == "rag":
                del sys.modules[m]
        import rag as dev_rag       # noqa: E402
        print(f"    dev 代码 IDX_VERSION: {dev_rag.IDX_VERSION}"
              f"　→ 升级后需重建索引（旧索引仍可读，但慢）")
    except Exception:               # noqa: BLE001
        pass

    # P2-6：契约漂移（reference/ 不在部署范围内，必须显式提示）
    drift = contract_drift(target)
    if drift:
        print("\n  契约漂移（reference/，本工具不部署）：")
        for name, ha, hb in drift:
            state = "目标库缺失" if not ha else ("开发仓缺失" if not hb else "内容不同")
            print(f"    {name:32} {state}")
        print("    → 若本次代码依赖新契约，请手动同步对应文件后再升级。")
    return 0


def _copy_code(target: Path) -> int:
    n = 0
    for d in CODE_DIRS:
        src_dir = TEMPLATE / d
        if not src_dir.is_dir():
            continue
        for sp in src_dir.rglob("*"):
            if not sp.is_file() or not _is_code(sp) or "__pycache__" in sp.parts:
                continue
            dp = target / d / sp.relative_to(src_dir)
            dp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sp, dp)
            n += 1
    # 清掉目标库的 __pycache__，避免升级后跑的还是旧字节码
    for pc in (target / "pipeline").rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)
    return n


def _run(argv: List[str], cwd: Path, timeout: int = 1800, env: Dict = None) -> Dict:
    e = dict(os.environ)
    e.pop("KB_ROOT", None)
    if env:
        e.update(env)
    t0 = time.time()
    r = subprocess.run([str(a) for a in argv], cwd=str(cwd), capture_output=True,
                       text=True, timeout=timeout, env=e)
    return {"code": r.returncode, "out": r.stdout or "", "err": r.stderr or "",
            "secs": round(time.time() - t0, 1)}


def _backup(target: Path) -> Path:
    """升级前备份：打包 pipeline/ 与 .kb/state/（代码 + 状态），足够回滚。"""
    bdir = target / "backups" / "pre-upgrade"
    bdir.mkdir(parents=True, exist_ok=True)
    out = bdir / f"pre-upgrade-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        for d in ("pipeline", "scripts"):
            if (target / d).exists():
                tf.add(target / d, arcname=d,
                       filter=lambda ti: None if "__pycache__" in ti.name else ti)
        if (target / ".kb" / "state").exists():
            tf.add(target / ".kb" / "state", arcname=".kb/state")
    return out


def _failing_checks(target: Path) -> set:
    """当前失败的检查项名集合（用于「不引入新失败」的对比）。只读。"""
    r = _run([PY, target / "pipeline" / "kb_verify.py", "--root", str(target), "--json"], target)
    try:
        d = json.loads(r["out"][r["out"].find("{"):])
        return {c["name"] for c in d["checks"] if c.get("pass") is False}
    except (ValueError, KeyError, json.JSONDecodeError):
        return set()


def _verify(target: Path, baseline_fails: set = frozenset()) -> int:
    """升级后验证。返回**新增**失败项数。

    为什么用「新增」而不是「绝对为 0」：目标库自身的巡检问题（超长/孤儿/死链…）
    与升级无关，升级前就存在。判据应当是「升级不引入新的失败」。
    """
    fails = 0
    print("\n  ── 验证 ──")
    # 1) 契约/校验/巡检/探针
    r = _run([PY, target / "pipeline" / "kb_verify.py", "--root", str(target), "--json"], target)
    try:
        d = json.loads(r["out"][r["out"].find("{"):])
        bad = {c["name"] for c in d["checks"] if c.get("pass") is False}
        new_bad = bad - set(baseline_fails)
        pre = f"（升级前已有 {len(bad & set(baseline_fails))} 项）" if baseline_fails else ""
        print(f"    kb verify: {d['verdict']}{pre}"
              + (f"　新增失败：{sorted(new_bad)}" if new_bad else "　无新增失败 ✓"))
        fails += len(new_bad)
    except (ValueError, KeyError):
        print(f"    kb verify: 输出无法解析（rc={r['code']}）")
        fails += 1
    # 2) 索引格式与查询
    idx = _index_state(target)
    print(f"    索引: {idx['file']} · version {idx['version']}"
          + (f" · {idx['struct']}" if idx.get("struct") else ""))
    q = _run([PY, target / "pipeline" / "rag.py", "query", "知识库怎么备份", "--root", str(target),
              "--top", "3", "--json", "--no-record"], target)
    try:
        hits = json.loads(q["out"][q["out"].find("{"):]).get("hits") or []
        print(f"    查询冒烟: {len(hits)} 条命中")
        fails += 0 if hits else 1
    except (ValueError, json.JSONDecodeError):
        print("    查询冒烟: 失败")
        fails += 1
    # 3) 索引自检：v7 必须不再有 tf、必须有 postings
    try:
        payload, ver = _load_vault_index(target)
        ok_v7 = bool(payload) and "postings" in payload and "tf" not in payload
        print(f"    索引结构: version {ver} · "
              f"{'postings（v7 ✓，已无需 tf）' if ok_v7 else '仍是旧格式 → 建议重建'}")
        if payload is not None and not ok_v7:
            fails += 1
    except Exception as e:      # noqa: BLE001
        print(f"    索引结构: 探测失败 {type(e).__name__}")
        fails += 1
    # 4) 状态文件：旧位置可读
    legacy_probe = target / ".memory_ingest_state.json"
    if legacy_probe.exists():
        print(f"    旧位置状态仍可读（由回退机制覆盖）：{legacy_probe.name}")
    return fails


def cmd_apply(args, target: Path) -> int:
    if target == REPO.resolve() or REPO.resolve() in target.parents:
        print("✗ 拒绝：目标是开发仓自身")
        return 2
    if not (target / "pipeline").is_dir():
        print(f"✗ {target} 不是 kb-kit 库")
        return 2
    added, changed, _ = diff_code(target)
    print(f"🚀 升级　{TEMPLATE} → {target}")
    print(f"  待变更：新增 {len(added)} · 变更 {len(changed)}")
    args._baseline = _failing_checks(target)
    print(f"  升级前失败项基线：{sorted(args._baseline) or '无'}"
          f"（判据是「不引入新失败」，不是「绝对全绿」）")
    if not args.yes:
        print("\n  这是**写操作**：会覆盖目标库的 pipeline/ 与 scripts/（不动任何 .md 内容）。")
        print("  确认后加 --yes 重跑；或先 `plan` 看清单、`rehearse` 演练。")
        return 1
    print("\n  ① 备份 …")
    bk = _backup(target)
    print(f"     {bk}（{bk.stat().st_size / 1048576:.1f} MB）")
    print("\n  ② 复制代码 …")
    n = _copy_code(target)
    print(f"     复制 {n} 个文件（跳过所有 .md 内容）")
    print("\n  ③ 重建索引（格式升级必需）…")
    r = _run([PY, target / "pipeline" / "rag.py", "index", "--root", str(target), "--full"], target)
    print(f"     rc={r['code']} {(r['out'] or r['err']).strip().splitlines()[-1:]}")
    fails = _verify(target, args._baseline)
    print(f"\n  {'✅ 升级完成' if fails == 0 else f'⚠️ 有 {fails} 项验证未过'}")
    # P2-6：升级后再次提示契约漂移（reference/ 不会被本工具覆盖）
    drift = contract_drift(target)
    if drift:
        print(f"\n  {_contract_warning(drift)}")
        print("    处理：手动复制 template-vault/reference/<文件> 到目标库，再跑 kb verify。")
    print(f"  回滚：`tar -xzf {bk} -C {target}` 然后 `git -C {target} checkout -- .`")
    return 0 if fails == 0 else 1


def cmd_rehearse(args) -> int:
    """在副本上完整演练：复制目标库（只读源）→ 应用 → 验证。"""
    src = Path(args.to).resolve()
    if not (src / "pipeline").is_dir():
        print(f"✗ {src} 不是 kb-kit 库")
        return 2
    dst = TESTENV / "deploy_rehearsal"
    print(f"🧪 演练　{src} → {dst}\n")
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    print("  ① 复制目标库（跳过 backups/ logs/ 以省空间；其余含 .git 一并复制）…")
    shutil.copytree(src, dst,
                    ignore=shutil.ignore_patterns("backups", "logs", "__pycache__"),
                    symlinks=True)
    before = _index_state(dst)
    print(f"     副本索引：{before['file']} · version {before['version']}")
    added, changed, _ = diff_code(dst)
    print(f"     待变更：新增 {len(added)} · 变更 {len(changed)}")
    baseline_fails = _failing_checks(dst)
    print(f"     升级前失败项基线：{sorted(baseline_fails) or '无'}")
    print("\n  ② 备份 → 复制代码 → 重建索引 …")
    bk = _backup(dst)
    print(f"     备份 {bk.name}（{bk.stat().st_size / 1048576:.1f} MB）")
    n = _copy_code(dst)
    print(f"     复制 {n} 个文件")
    r = _run([PY, dst / "pipeline" / "rag.py", "index", "--root", str(dst), "--full"], dst)
    print(f"     重建索引 rc={r['code']} · {(r['out'] or r['err']).strip().splitlines()[-1:][0][:70] if (r['out'] or r['err']).strip() else ''}")
    fails = _verify(dst, baseline_fails)
    print(f"\n  {'✅ 演练通过（该流程可用于生产）' if fails == 0 else f'⚠️ 演练有 {fails} 项未过 —— 先别上生产'}")
    return 0 if fails == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="把 dev 仓运行时代码升级到某个 kb-kit 库")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("plan", help="只读：列出将变更的文件")
    p1.add_argument("--to", default="/home/mushan/kb-kit-pure")
    p2 = sub.add_parser("apply", help="执行升级（需 --yes）")
    p2.add_argument("--to", default="/home/mushan/kb-kit-pure")
    p2.add_argument("--yes", action="store_true")
    p3 = sub.add_parser("rehearse", help="在副本上演练")
    p3.add_argument("--to", default="/home/mushan/kb-kit-pure")
    a = ap.parse_args()
    if a.cmd == "plan":
        return cmd_plan(a)
    if a.cmd == "rehearse":
        return cmd_rehearse(a)
    return cmd_apply(a, Path(a.to).resolve())


if __name__ == "__main__":
    sys.exit(main())
