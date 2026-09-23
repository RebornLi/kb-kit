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

    def __init__(self, vault_root):
        self.root = Path(vault_root)
        self.dir = self.root / ".kb" / "state"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name):
        return self.dir / name

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
        """加载状态文件。返回 dict，文件不存在返回 default 或 {}。"""
        p = self._path(name)
        if not p.exists():
            return default if default is not None else {}
        lock_fd, _ = self._lock(p, exclusive=False)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return default if default is not None else {}
        finally:
            self._unlock(lock_fd)

    def save(self, name, data):
        """保存状态文件（原子写入：先写临时文件再 rename）。"""
        p = self._path(name)
        lock_fd, _ = self._lock(p, exclusive=True)
        try:
            # 原子写入：临时文件 → rename
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")
            tmp.rename(p)
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
                tmp = p.with_suffix(p.suffix + ".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
                tmp.rename(p)
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
LEGACY_PATHS = {
    "feedback_state.json": ".kb/feedback_state.json",
    "memory_state.json": ".kb/memory_state.json",
    "sync_state.json": ".kb/sync_state.json",
    "ingest_state.json": ".kb/ingest_state.json",
    "query_cache.json": ".kb/query_cache.json",
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
