#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_worker.py — kb-kit 持久化查询 worker（供 DSH / OpenClaw 上下文钩子复用）。

## 为什么需要它

kb-context-dsh / kb-context-hook 之前每轮用户对话都 `execFile('python3', ['rag.py',
'query', ...])` —— 每次 spawn 一个全新 Python 进程，重新付一遍：

  · 解释器启动       ~30ms
  · import rag 等    ~30ms
  · **索引加载**       ~72ms（9.2MB pickle，含语义向量）
  ──────────────────────────────
  合计 ~130ms 的纯开销，而查询打分本身只要 ~60ms。

本 worker 把索引**加载一次**、常驻内存，之后每查询只做打分。索引文件 mtime 变了就
自动重载（kb-kit 的 cron 重建索引后，下一个查询自然拿到新索引，不会一直用旧的）。

## 协议（JSON-lines over stdio）

  stdin  ← 每行一个请求：{"query": str, "topN": int}
  stdout → 每行一个响应：{"ok": true, "hits": [...], "meta": {...}}
                           或 {"ok": false, "error": "..."}

任何单条查询失败都只返回 {"ok":false}，**绝不退出 worker**；worker 本身只在 stdin EOF
时退出（父进程关闭管道 = 正常回收）。

用法：python3 pipeline/kb_worker.py <vault_root>
"""
import contextlib
import io
import json
import os
import sys
from pathlib import Path


def _emit(obj: dict) -> None:
    """写一行 JSON 并立即 flush —— 父进程靠换行解析，flush 靠实时性。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    pdir = root / "pipeline"
    if str(pdir) not in sys.path:
        sys.path.insert(0, str(pdir))

    # import 失败（旧库没有 rag 之类）→ 启动即报错，父进程会回退到逐次 execFile
    try:
        import rag
    except Exception as e:      # noqa: BLE001
        _emit({"ok": False, "error": f"import rag failed: {type(e).__name__}"})
        return 2

    payload = None
    payload_mtime = -1.0

    def reload_if_stale() -> bool:
        """索引 mtime 未变 → 复用已加载的 payload；变了/没加载 → 重新 load。"""
        nonlocal payload, payload_mtime
        try:
            idx = rag.index_file(root / "vector index")
        except Exception:       # noqa: BLE001
            return False
        if idx is None:
            return False
        try:
            mtime = idx.stat().st_mtime
        except OSError:
            return False
        if payload is not None and mtime == payload_mtime:
            return True
        try:
            p, _ver = rag.load_index(idx)
        except Exception:       # noqa: BLE001
            payload = None
            return False
        if not p:
            return False
        payload = p
        payload_mtime = mtime
        return True

    # 用 cmd_query 内部逻辑（-answer 关、no_record 开，避免写使用信号污染 agent_hits）
    # 但把它的 stdout 重定向到缓冲，取回结构化 hits —— 这样与 CLI 行为 100% 一致。
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        seq = req.get("seq")            # 父进程用它把响应匹配回请求（原样回显）
        q = str(req.get("query") or "").strip()
        top_n = int(req.get("topN") or 0)
        if top_n <= 0:
            top_n = 3
        ctx_n = int(req.get("context") or 240)   # 片段窗口，与 CLI --context 对齐
        if ctx_n <= 0:
            ctx_n = 240
        if not q:
            _emit({"ok": True, "seq": seq, "hits": [], "meta": {}})
            continue
        if not reload_if_stale():
            _emit({"ok": False, "seq": seq, "error": "index unavailable"})
            continue
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rag.cmd_query(root, q, top_n, False, as_json=True, context=ctx_n,
                              no_record=True)
            data = json.loads(buf.getvalue().strip() or "{}")
            _emit({"ok": True, "seq": seq, "hits": data.get("hits") or [],
                   "meta": data.get("meta") or {}})
        except Exception as e:      # noqa: BLE001  单条失败绝不退出 worker
            _emit({"ok": False, "seq": seq, "error": f"{type(e).__name__}: {str(e)[:160]}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
