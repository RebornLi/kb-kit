#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_llm_tps.py — 本地 chat 模型吞吐基准（tok/s）。

用途：回答"本地模型每秒能出多少 token"，并区分两个**不能混为一谈**的口径：

  · 总输出 tok/s   = (reasoning + 正文) / 墙钟   ← 决定**成本与延迟预算**
  · 正文 tok/s     = 只算正文 / 墙钟             ← 决定**有用产出速度**

为什么必须分开：`ornith1.5-35b` 是**带 reasoning 的模型**。实测 max_tokens=512 时
reasoning 吃掉 100% 的预算、正文 0 token —— 只看"总 tok/s"会得出"很快"的错误结论，
而实际一个字的答案都没产出。`max_tokens` 必须覆盖思考再留正文额度。

实测基线（2026-10-01，GB10 / NVFP4+MTP / gpu-memory-utilization 0.70）：
  单流 93 tok/s（正文 76）· 并发 24 总吞吐 509 tok/s（正文 347）· 24 并发后饱和

用法:
  python3 scripts/bench_llm_tps.py [--max-tokens 2048] [--concurrency 1,2,4,8,16,24]
"""
"""重测：给足预算(2048)，区分【总输出】与【正文(有用)】两个 tok/s，并找并发饱和点。"""
import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,'pipeline')
from kb_common import model_config
c = model_config()
URL = c['base_url'].rstrip('/') + "/chat/completions"; KEY = c['api_key']; MODEL = c['chat_model']

def one(i, mt):
    payload = json.dumps({"model": MODEL, "temperature": 0.0, "max_tokens": mt,
        "messages":[{"role":"user","content":f"请详细写一段关于向量检索与 BM25 对比的技术说明（第 {i} 份）。"}]}).encode()
    req = urllib.request.Request(URL, data=payload,
        headers={"Content-Type":"application/json","Authorization":f"Bearer {KEY}"})
    t0=time.time()
    with urllib.request.urlopen(req, timeout=900) as r: d=json.loads(r.read())
    dt=time.time()-t0; u=d.get('usage') or {}
    ct=u.get('completion_tokens') or 0
    rt=(u.get('completion_tokens_details') or {}).get('reasoning_tokens') or 0
    return {"secs":dt,"ct":ct,"rt":rt,"content":ct-rt,
            "finish":(d.get('choices') or [{}])[0].get('finish_reason')}

MT=2048
print(f"═══ max_tokens={MT}，区分两个口径 ═══")
print(f"{'并发':>4} {'墙钟':>7} {'总tok':>6} {'总tok/s':>8} {'正文tok':>7} {'正文tok/s':>9} {'单流tok/s':>9} {'思考占比':>7} finish")
best=(0,0)
for conc in (1,2,4,8,16,24):
    t0=time.time()
    with ThreadPoolExecutor(max_workers=conc) as ex:
        rs=list(ex.map(lambda i: one(i,MT), range(conc)))
    wall=time.time()-t0
    tot=sum(r["ct"] for r in rs); con=sum(r["content"] for r in rs); rea=sum(r["rt"] for r in rs)
    per=[r["ct"]/r["secs"] for r in rs]
    print(f"{conc:>4} {wall:>6.1f}s {tot:>6} {tot/wall:>8.1f} {con:>7} {con/wall:>9.1f} "
          f"{sum(per)/len(per):>9.1f} {rea/max(tot,1):>7.1%} {set(r['finish'] for r in rs)}")
    if tot/wall > best[1]: best=(conc, tot/wall)
print(f"\n  总输出吞吐最优：并发 {best[0]} → **{best[1]:.0f} tok/s**")
