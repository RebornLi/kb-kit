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

  const lines = [cfg.prefix, '```'];
  let acc = '';
  for (const h of hits) {
    const domain = h.domain ? ` · ${h.domain}` : '';
    const imp = h.importance != null ? `，importance ${h.importance}` : '';
    const line = `• ${h.title}（${h.path}${domain}，分 ${h.score.toFixed(3)}${imp}）` +
      (h.snippet ? `\n  ${h.snippet}` : '');
    const withNewline = line + '\n';
    // 字节封顶：本命中加入后若超上限则停止（与 DSH 实现同口径）
    if (Buffer.byteLength(acc) + Buffer.byteLength(withNewline) > cfg.maxBytes) break;
    lines.push(line.trimEnd());
    acc += withNewline;
  }
  if (lines.length === 2) return ''; // 只有 prefix + 开围栏，无命中贡献 → 跳过

  lines.push('```', '（以上为知识库相关片段，如与本轮问题无关请忽略）');
  return lines.join('\n');
}

/**
 * 命中集的稳定去重键（顺序无关）：path::score 排序后拼接。
 * 同一会话内相同命中集不重复注入，保护 prompt 缓存。
 * @param {object[]} hits
 * @returns {string}
 */
export function hitsKey(hits) {
  return (hits || [])
    .map((h) => `${h.path}::${h.score}`)
    .sort()
    .join('|');
}
