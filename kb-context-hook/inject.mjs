// ============================================================
// inject.mjs — 纯注入块构造 + 去重键（无 openclaw / 无 I/O，可单测）
//
// 与 kb-context-dsh/src/inject.js 语义一致：
//   - prefix + 开围栏 → 命中行 → 闭围栏 + 免责说明（balanced fence）
//   - 按 UTF-8 字节数封顶（Buffer.byteLength），绝不撑爆 prompt 缓存
//   - hitsKey 对同一命中集稳定（顺序无关）
// ============================================================

/**
 * 把规范化命中对象数组拼成注入文本。
 * @param {object[]} hits — queryKB() 过滤/截断后的命中对象
 * @param {Record<string, any>} cfg
 * @returns {string} — 无可用命中时返回 ''（调用方跳过注入）
 */
export function buildInjectedText(hits, cfg) {
  if (!hits || hits.length === 0) return '';

  // prefix + opening fence: the fence wraps the hit lines so the model treats
  // them as external reference data (and does not execute any instructions
  // that might appear inside a hit snippet).
  const head = cfg.prefix + '\n```\n';
  const tail = '```\n（以上为知识库相关片段，如与本轮问题无关请忽略）';
  // ⚠️ 字节封顶必须把 head/tail 也算进去 —— 初版只累计命中行，导致整块超出
  //   maxBytes（前缀 + 围栏 + 免责说明约 50+ 字节没算）。
  const budget = Math.max(0, cfg.maxBytes - Buffer.byteLength(head) - Buffer.byteLength(tail));
  const lines = [cfg.prefix, '```'];
  let acc = 0;
  for (const h of hits) {
    const domain = h.domain ? ` · ${h.domain}` : '';
    const imp = h.importance != null ? `，importance ${h.importance}` : '';
    const line = `• ${h.title}（${h.path}${domain}，分 ${h.score.toFixed(3)}${imp}）` +
      (h.snippet ? `\n  ${h.snippet}` : '');
    const withNewline = line + '\n';
    // Byte cap: never exceed cfg.maxBytes of injected text for this turn.
    if (acc + Buffer.byteLength(withNewline) > budget) break;
    lines.push(line.trimEnd());
    acc += Buffer.byteLength(withNewline);
  }
  if (lines.length === 2) return ''; // only prefix + opening fence, no hits contributed → skip
  lines.push('```', '（以上为知识库相关片段，如与本轮问题无关请忽略）');
  return lines.join('\n');
}

/**
 * Stable dedupe key for a set of hits: the sorted list of hit identities.
 * Two turns that resolve to the exact same hits should not re-inject — this
 * protects the prompt cache across near-identical questions within a session.
 * @param {object[]} hits
 * @returns {string}
 */
export function hitsKey(hits) {
  return (hits || [])
    .map((h) => `${h.path}::${h.score}`)
    .sort()
    .join('|');
}
