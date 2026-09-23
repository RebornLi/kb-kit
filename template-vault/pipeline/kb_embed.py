#!/usr/bin/env python3
# ============================================================
# kb_embed.py —— RSI 语义向量地基（方案 C / Layer 0）
# ------------------------------------------------------------
# 作用：给流水线提供可选的"语义向量"路径，替换/补上原来的字面 char-vec。
#   - rag / kb_rsi 的"命中/遗漏"精度基线从此可以上升到语义层，检索·去重·canary 全受益。
#   - 这是方案 C 的精度底座：没有真语义对错，上层"现实验证"无从谈起。
#
# 接线（P4 起沿用 rag.py 的铁律，绝不擅自改动）：
#   - 端点：ORNITH_BASE_URL + "/embeddings"（vLLM OpenAI 兼容）—— 与 chat 同源。
#   - 认证：Authorization: Bearer {ORNITH_API_KEY}（与 rag.py 完全一致）。
#   - 模型：ORNITH_EMBED_MODEL（缺省空串，vLLM 用 server 默认）；也可照 rag 用固定名。
#   - 纪律：ORNITH_BASE_URL / ORNITH_API_KEY 任一缺失 → 返回 None（不连写死 LAN、不报错爆炸）。
#   - 降级：不可用 → 调用方原样退回 char-vec，**默认行为零变化**。
#   - 节流：available() 带缓存 + 300s 探测间隔，绝不塞满 hot 路径；best-effort，静默降级。
#
# 用法:
#   python3 pipeline/kb_embed.py probe        # 探测端点是否可用 + 返回维度
#   python3 pipeline/kb_embed.py vec "中文句子"  # 打印单句归一化向量长度（调试用）
# ------------------------------------------------------------
import json, math, os, sys, time
from urllib import request as _urlreq
from urllib import error as _urlerr

EMBED_PATH = "/embeddings"
_TIMEOUT = 10          # embedding 单次超时（秒）：比 chat 短，失败要快退
_TIMEOUT_PROBE = 12
_RETRY = 2             # 网络抖动重试
_CACHE_LIMIT = 8192    # 进程内缓存条数上限（文本 → 归一化向量）

# ── 进程内缓存（module 级）─────────────────────────────────
_cache = {}                  # 文本 → 归一化 list[float]
_probe = None                # None=未知; True/False=探测结论
_last_probe_ts = 0.0
_PROBE_INTERVAL = 300.0      # 两次探测的最小间隔（秒），避免每句都试网


def _base_url():
    # embedding 专用地址（可与 chat 服务不同端口，本仓库 embedding 跑在独立 vLLM）；
    # 缺省回退 ORNITH_BASE_URL（向后兼容：只设 BASE_URL 时二者同源）。
    return (os.environ.get("ORNITH_EMBED_URL") or os.environ.get("ORNITH_BASE_URL") or "").strip()


def _api_key():
    return (os.environ.get("ORNITH_API_KEY") or "").strip()


def _model():
    return (os.environ.get("ORNITH_EMBED_MODEL") or "").strip()


def _post(url, payload):
    """POST JSON 到 embedding 端点，返回解析后的 dict；失败抛异常由调用方捕获。"""
    data = json.dumps(payload).encode("utf-8")
    req = _urlreq.Request(url.rstrip("/") + EMBED_PATH, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    key = _api_key()
    if key:
        req.add_header("Authorization", "Bearer " + key)
    with _urlreq.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _first_vector(payload):
    """从 OpenAI 兼容响应里取第一条向量；兼容 {data:[{embedding}]} 与顶层 embedding。"""
    if isinstance(payload, dict) and "data" in payload and payload["data"]:
        first = payload["data"][0]
        return first["embedding"] if isinstance(first, dict) else first
    # 降级：某些 server 直接返回 {"embedding": [...]}
    if isinstance(payload, dict) and isinstance(payload.get("embedding"), list):
        return payload["embedding"]
    raise ValueError("无法解析 embedding 响应格式")


def _normalize(vec):
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def available():
    """embedding 端点是否可用。带缓存 + 节流；always returns bool（绝不抛）。"""
    global _probe, _last_probe_ts
    now = time.time()
    if _probe is not None and (now - _last_probe_ts) < _PROBE_INTERVAL:
        return _probe
    _probe = _probe_once()
    _last_probe_ts = now
    return _probe


def _probe_once():
    """探一次：空模型试端点。连不上/格式不对 → False（best-effort，吞异常）。"""
    url = _base_url()
    if not url:
        return False
    payload = {"input": "probe", "model": _model()}
    for attempt in range(_RETRY):
        try:
            r = _post(url, payload)
            vec = _first_vector(r)
            return isinstance(vec, list) and len(vec) > 0
        except (_urlerr.URLError, OSError, ValueError, KeyError, IndexError):
            if attempt + 1 >= _RETRY:
                return False
            time.sleep(0.3 * (attempt + 1))


def embed(text):
    """返回 text 的归一化 embedding（list[float]）；不可用 → None（调用方降级）。"""
    if text is None:
        return None
    if not available():
        return None
    if text in _cache:
        return _cache[text]
    url = _base_url()
    if not url:
        return None
    payload = {"input": text, "model": _model()}
    for attempt in range(_RETRY):
        try:
            r = _post(url, payload)
            vec = _normalize(_first_vector(r))
            if len(_cache) < _CACHE_LIMIT:
                _cache[text] = vec
            return vec
        except (_urlerr.URLError, OSError, ValueError, KeyError, IndexError):
            if attempt + 1 >= _RETRY:
                return None
            time.sleep(0.3 * (attempt + 1))


# ── CLI（供人/CI 手动验证地基是否接通）─────────────────────
def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="kb_embed: 语义向量地基探测/调试")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe")
    p.add_argument("--text", default="probe")
    pa = sub.add_parser("vec")
    pa.add_argument("text")

    args = ap.parse_args(argv)
    if args.cmd == "probe":
        ok = available()
        url = _base_url()
        print(json.dumps({
            "embedding_available": bool(ok),
            "base_url_set": bool(url),
            "model": _model() or "<server-default>",
            "hint": ("未设 ORNITH_EMBED_URL/ORNITH_BASE_URL，embedding 不可用（会降级字面向量）"
                     if not url else
                     "端点可达且返回向量层 → Layer 0 生效" if ok else
                     "端点不可达或格式不符；请检查 ORNITH_EMBED_URL 是否指向 vLLM 的 /embeddings"
                     "（默认回退 ORNITH_BASE_URL），若模型名非默认请设 ORNITH_EMBED_MODEL"),
        }, ensure_ascii=False, indent=2))
        return 0 if ok else 2
    # vec
    v = embed(args.text)
    if v is None:
        print("None（embedding 不可用，调用方会降级）")
        return 2
    print(f"len={len(v)}  head={[round(x, 4) for x in v[:6]]}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
