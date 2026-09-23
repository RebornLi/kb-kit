// ============================================================
// inject.js — pure reference-block builder + dedupe keys.
//
// No cordis / child_process / I/O here: this is the testable core that turns a
// hit list into the fenced "📚 本地知识库命中" block the model sees as an
// injected user message, and the helper that dedupes repeated injections.
// ============================================================

/**
 * Build the injected reference text from normalized hits.
 * Byte-capped so a long hit list can never blow up the prompt cache.
 * @param {object[]} hits   — from queryKB() (already min-score/limit filtered)
 * @param {Record<string, any>} cfg
 * @returns {string}        — '' when empty (caller skips the injection)
 */
export function buildInjectedText(hits, cfg) {
  if (!hits || hits.length === 0) return '';

  // prefix + opening fence: the fence wraps the hit lines so the model treats
  // them as external reference data (and does not execute any instructions
  // that might appear inside a hit snippet).
  const lines = [cfg.prefix, '```'];
  let acc = '';
  for (const h of hits) {
    const domain = h.domain ? ` · ${h.domain}` : '';
    const imp = h.importance != null ? `，importance ${h.importance}` : '';
    const line = `• ${h.title}（${h.path}${domain}，分 ${h.score.toFixed(3)}${imp}）` +
      (h.snippet ? `\n  ${h.snippet}` : '');
    const withNewline = line + '\n';
    // Byte cap: never exceed cfg.maxBytes of injected text for this turn.
    if (Buffer.byteLength(acc) + Buffer.byteLength(withNewline) > cfg.maxBytes) break;
    lines.push(line.trimEnd());
    acc += withNewline;
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
