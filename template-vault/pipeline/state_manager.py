#!/usr/bin/env python3
# ============================================================
# state_manager.py —— 状态文件统一管理 + 文件锁（FR-3.3.6 + FR-3.1.7）
#   统一管理 .kb/ 下的所有状态文件，带 fcntl.flock 文件锁
#   用法:
#     from state_manager import StateStore
#     store = StateStore(vault_root)
#     data = store.load("feedback_state.json")
#     store.save("feedback_state.json", data)
#     store.update("feedback_state.json", lambda d: d.setdefault("hits", []).append(x))
# ============================================================
import json, os, tempfile
from pathlib import Path

# fcntl 仅 Unix 可用；Windows 降级为无锁（单进程安全）
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False


class StateStore:
    """状态文件统一管理器。

    状态文件统一存放在 vault_root/.kb/state/ 目录。
    支持 load/save/update 操作，带文件锁（fcntl.flock）。
    """

    def __init__(self, vault_root, legacy=()):
        self.root = Path(vault_root)
        self.dir = self.root / STATE_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        # 旧路径（vault 相对），按顺序只读回退；写永远只写规范路径
        self.legacy = tuple(legacy)

    def _path(self, name):
        return self.dir / name

    def _atomic_write(self, p, text):
        """原子写入：先写同目录临时文件，再 os.replace 覆盖目标。

        必须用 os.replace 而非 Path.rename —— Windows 上 rename 在目标已存在时
        会抛 FileExistsError，导致第二次起状态写入静默失败（调用方多吞异常）。
        os.replace 在 POSIX/Windows 均为原子覆盖。
        失败时清理残留临时文件。
        """
        # 实现已归一到 kb_common.atomic_write_text（原先这里有第二份同构实现）
        from kb_io import atomic_write_text
        atomic_write_text(p, text)

    def _lock(self, path, exclusive=True):
        """获取文件锁。返回 (fd, lock_obj) 或 (None, None)。"""
        if not _HAS_FCNTL:
            return None, None
        fd = open(path, "a", encoding="utf-8")
        try:
            fcntl.flock(fd.fileno(),
                        fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            return fd, True
        except (OSError, IOError):
            fd.close()
            return None, None

    def _unlock(self, fd):
        """释放文件锁。"""
        if fd is None:
            return
        try:
            if _HAS_FCNTL:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
        except (OSError, IOError):
            pass

    def load(self, name, default=None):
        """加载状态文件。返回 dict，文件不存在返回 default 或 {}。

        规范路径没有时，按 `legacy`（构造时传入）+ `LEGACY_PATHS` 表回退到旧位置 ——
        这样迁移不需要一次性搬文件，旧库升级后**立刻能读到原有状态**，不会"状态凭空清零"。
        """
        p = self._path(name)
        if not p.exists():
            p = self.legacy_read_path(name)
            if p is None:
                return default if default is not None else {}
        lock_fd, _ = self._lock(p, exclusive=False)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return default if default is not None else {}
        finally:
            self._unlock(lock_fd)

    def legacy_read_path(self, name):
        """规范文件缺失时，返回第一个存在的旧路径（只读回退）；都没有返回 None。"""
        if self._path(name).exists():
            return self._path(name)
        cands = list(self.legacy)
        if name in LEGACY_PATHS:
            cands.append(LEGACY_PATHS[name])
        for rel in cands:
            lp = self.root / rel
            if lp.exists():
                return lp
        return None
        lock_fd, _ = self._lock(p, exclusive=False)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return default if default is not None else {}
        finally:
            self._unlock(lock_fd)

    def save(self, name, data):
        """保存状态文件（原子写入：临时文件 → os.replace）。"""
        p = self._path(name)
        lock_fd, _ = self._lock(p, exclusive=True)
        try:
            self._atomic_write(p, json.dumps(data, ensure_ascii=False, indent=2))
        finally:
            self._unlock(lock_fd)

    def update(self, name, fn, default=None):
        """原子更新：load → fn(data) → save。

        Args:
            name: 状态文件名
            fn: 更新函数，接收 dict 返回 dict
            default: 文件不存在时的初始值

        Returns:
            更新后的 data
        """
        p = self._path(name)
        lock_fd, _ = self._lock(p, exclusive=True)
        data = default if default is not None else {}
        try:
            if p.exists():
                text = p.read_text(encoding="utf-8")
                if text.strip():
                    data = json.loads(text)
            data = fn(data)
            if data is not None:
                self._atomic_write(p, json.dumps(data, ensure_ascii=False, indent=2))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            data = default if default is not None else {}
        finally:
            self._unlock(lock_fd)
        return data

    def exists(self, name):
        """检查状态文件是否存在。"""
        return self._path(name).exists()

    def remove(self, name):
        """删除状态文件。"""
        p = self._path(name)
        if p.exists():
            p.unlink()


# ── 旧状态文件路径映射（向后兼容）──────────────────────────────
# 规范状态目录（**唯一**约定）：<vault>/.kb/state/
STATE_DIR = Path(".kb") / "state"


def state_path(root, name) -> Path:
    """状态文件的**规范**路径（写只写这里）。父目录由 atomic_write_text 自动创建。"""
    return Path(root) / STATE_DIR / name


def read_state_path(root, name, legacy=()) -> "Path | None":
    """读路径：规范文件在就用它，否则按 `legacy` → `LEGACY_PATHS` 回退；都没有返回 None。"""
    canon = state_path(root, name)
    if canon.exists():
        return canon
    cands = list(legacy)
    if name in LEGACY_PATHS:
        cands.append(LEGACY_PATHS[name])
    for rel in cands:
        lp = Path(root) / rel
        if lp.exists():
            return lp
    return None


def read_json(root, name, legacy=(), default=None):
    """读 JSON 状态（规范优先，旧路径回退）。损坏/缺失 → default。"""
    p = read_state_path(root, name, legacy)
    if p is None:
        return default if default is not None else {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return default if default is not None else {}


def write_json(root, name, data) -> Path:
    """写 JSON 状态到**规范**路径（原子写）。"""
    from kb_io import atomic_write_text        # 延迟 import：避开模块级循环
    p = state_path(root, name)
    atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=2))
    return p


def read_jsonl(root, name, legacy=()):
    """读 JSONL 状态（规范优先，旧路径回退）。逐行容错。"""
    p = read_state_path(root, name, legacy)
    if p is None:
        return []
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except (OSError, UnicodeDecodeError):
        return []
    return out


def append_jsonl(root, name, rec) -> Path:
    """向**规范**路径追加一行 JSONL（父目录自动创建）。"""
    p = state_path(root, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p


# 旧路径表：迁移期只读回退。统一约定为 .kb/state/<name>（见 STATE_DIR）。
# 键 = 规范文件名；值 = 旧位置（vault 相对）。
LEGACY_PATHS = {
    "feedback_state.json": ".kb/feedback_state.json",
    "memory_state.json": ".kb/memory_state.json",
    "sync_state.json": ".kb/sync_state.json",
    "ingest_state.json": ".kb/ingest_state.json",
    "query_cache.json": ".kb/query_cache.json",
    # 曾经散在各处的运行态（根 dotfile / pipeline 下 / .kb/ 直下）
    "memory_ingest_state.json": ".memory_ingest_state.json",
    "kb_meta_state.json": ".kb_meta_state.json",
    "kb_meta_config.json": ".kb_meta_config.json",
    "kb_meta_proposals.jsonl": ".kb_meta_proposals.jsonl",
    "kb_l4_verdicts.jsonl": ".kb_l4_verdicts.jsonl",
    "kb_l4_state.json": ".kb_l4_state.json",
    "kb_l4_config.json": ".kb_l4_config.json",
    "kb_l5_state.json": ".kb_l5_state.json",
    "kb_l5_policy.json": ".kb_l5_policy.json",
    "kb_selfweight_proposals.jsonl": "pipeline/.kb_selfweight_proposals.jsonl",
    "kb_selfweight_applied.json": "pipeline/.kb_selfweight_applied.json",
    "kb_retriage_proposals.jsonl": "pipeline/.kb_retriage_proposals.jsonl",
    "kb_retriage_applied.json": "pipeline/.kb_retriage_applied.json",
    "kb_adaptretrieve_proposals.jsonl": "pipeline/.kb_adaptretrieve_proposals.jsonl",
    "kb_retrieval_adjust.json": "pipeline/.kb_retrieval_adjust.json",
    "promotions.jsonl": ".kb/promotions.jsonl",
    "curate_state.json": ".kb/curate_state.json",
    "curate_ledger.jsonl": ".kb/curate_ledger.jsonl",
    "curate_proposals.jsonl": ".kb/curate_proposals.jsonl",
    "kb_evolution.jsonl": "pipeline/.kb_evolution.jsonl",
    "kb_rsi_proposals.jsonl": "pipeline/.kb_rsi_proposals.jsonl",
    "curate_review.jsonl": ".kb/curate_review.jsonl",
    "kb-agent.json": "kb-agent.json",
    "kb_schema_migrate.jsonl": "pipeline/.kb_schema_migrate.jsonl",
    "recall_state.json": "pipeline/.recall_state.json",
    "memory_sync_state.json": ".memory_sync_state.json",
    "kb_contradictions.json": "pipeline/.kb_contradictions.json",
    "feedback_state.json.legacy": "pipeline/.feedback_state.json",
    "kb_engine_proposals.jsonl": "pipeline/.kb_engine_proposals.jsonl",
    "kb_rsi_config.json": "pipeline/.kb_rsi_config.json",
    "kb_query_proposals.jsonl": "pipeline/.kb_query_proposals.jsonl",
    "kb_query_state.json": "pipeline/.kb_query_state.json",
}


def migrate_legacy_state(vault_root):
    """将旧路径的状态文件迁移到 .kb/state/ 目录。"""
    store = StateStore(vault_root)
    root = Path(vault_root)
    migrated = []
    for name, legacy_rel in LEGACY_PATHS.items():
        legacy_path = root / legacy_rel
        new_path = store._path(name)
        if legacy_path.exists() and not new_path.exists():
            try:
                data = json.loads(legacy_path.read_text(encoding="utf-8"))
                store.save(name, data)
                migrated.append(name)
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                pass
    return migrated
