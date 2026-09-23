// ============================================================
// index.js — kb-context-dsh : DSH-native kb-kit context hook.
//
// A pure server-side cordis plugin. It does two things:
//   1. Registers an explicit `kb_query` model tool so the agent can search the
//      local kb-kit knowledge base on demand (returns structured hits).
//   2. Hooks agent/pre-step to auto-inject the top KB search hits into every
//      *user*-triggered turn as a fenced "📚 本地知识库命中" reference block,
//      mirroring the OpenClaw kb-context-hook — but written for the DSH cordis
//      runtime (next() gate + createUserMessage + per-session dedupe).
//
// Safety: the hook never throws into the agent loop, never blocks a reply, and
// skips silently on any failure/timeout/no-hit (best-effort by design).
//
// export contract (cordis loader): { name, inject, apply }
// ============================================================
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { loadConfig } from './config.js';
import { queryKB } from './kb.js';
import { buildInjectedText, hitsKey } from './inject.js';

export const name = 'kb-context-dsh';

// Host-provided capabilities this plugin touches: a model tool + a system
// prompt hint. No storage / llm / client needed (the hook shells to Python).
export const inject = ['tools', 'systemPrompt'];

export function apply(ctx) {
  const cfg = loadConfig();

  // Master switch. A disabled hook must never register anything heavy.
  if (cfg.enabled === false) {
    ctx.logger?.info?.(`[${name}] skipped: KB_HOOK_ENABLED=false`);
    return;
  }

  // Per-session last-injected key (WeakMap, auto-reclaimed on GC, no leak).
  // Scoped to this apply() call so each plugin load gets its own map.
  const lastKeyMap = new WeakMap();

  // --- system-prompt hint (best-effort) -------------------------------
  // Tells the model the block is external reference data it may ignore.
  try {
    ctx.systemPrompt.section({
      name: 'kb-context-hint',
      order: 1000,
      text:
        '（kb-context-dsh）用户的问题可能已在回合前自动注入本地知识库（kb-kit）的相关命中，' +
        '作为参考资料使用，与当前问题无关时可忽略。你也可随时调用 kb_query 工具主动检索知识库。',
    });
  } catch { /* section is advisory */ }

  // --- explicit kb_query model tool -----------------------------------
  ctx.tools.register(defineTool({
    name: 'kb_query',
    description:
      'Search the local kb-kit knowledge base (an Obsidian-style PARA vault) and return ' +
      'structured relevance hits with snippets. Use when the user asks about anything ' +
      'stored in the local knowledge base that you have not already been shown. Read-only: ' +
      'does not modify the vault. ' +
      `Each hit's snippet is truncated to ${cfg.snippetMax} chars.`,
    parameters: {
      query: {
        type: 'string',
        required: true,
        description: 'Natural-language query to search the knowledge base with.',
      },
      topN: {
        type: 'number',
        description: `Max hits to return (default ${cfg.topN}).`,
      },
    },
    async execute(args /* , exec */) {
      const query = String(args?.query ?? '').trim();
      if (!query) return { root: cfg.kbRoot, count: 0, hits: [] };
      const topN = Number.isInteger(args?.topN) && args.topN > 0 ? args.topN : cfg.topN;
      const hits = await queryKB({ ...cfg, topN }, query);
      return {
        root: cfg.kbRoot,
        count: hits.length,
        hits: hits.map((h) => ({
          path: h.path,
          title: h.title,
          domain: h.domain,
          score: Number(h.score.toFixed(4)),
          importance: h.importance,
          snippet: h.snippet,
        })),
      };
    },
    // dsh-tools v4 (cordis 4.x) requires output.render; supply a value
    // schema so defineTool accepts it (mirrors dsh-evolve's jsonOutput()).
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }],
    },
  }));

  // --- auto-inject on every user turn ---------------------------------
  ctx.on('agent/pre-step', async (payload, next) => {
    const decision = await next();
    // Only forward turns that actually proceed into the model.
    if (decision?.kind !== 'enter') return decision;

    // Gate on "is this a user turn": scan the outgoing messages for the last
    // real user text. Heartbeat / cron / sub-agent turns carry no user text and
    // are skipped here (zero overhead past the check).
    const query = queryFromMessages(payload?.messages);
    if (query === '' || !hasMeaningfulQuery(query)) {
      return decision;
    }

    try {
      const hits = await queryKB(cfg, query);
      if (!hits.length) return decision;

      const dedupe = cfg.dedupe !== false;
      if (dedupe) {
        // WeakMap keys MUST be objects; the host hands us an object session
        // (agent/pre-step payload.agent.session). A string-less/sessionless
        // dispatch is skipped here (inject anyway) instead of throwing.
        const sess = payload?.agent?.session ?? payload?.agent;
        if (sess && typeof sess === 'object') {
          const key = hitsKey(hits);
          const last = lastKeyMap.get(sess);
          if (last === key) return decision; // identical hit-set already shown this session
          lastKeyMap.set(sess, key);
        }
      }

      const text = buildInjectedText(hits, cfg);
      if (!text) return decision;

      const msg = createUserMessage({
        content: [{ type: 'text', text }],
        source: { kind: 'plugin', plugin: name, form: 'notice', summary: '知识库命中注入' },
      });
      return { ...decision, messages: [...decision.messages, msg] };
    } catch {
      // Retrieval failure / timeout / parse error → best-effort skip.
      // Never block the reply.
      return decision;
    }
  });
}

/**
 * Extract the latest real user text from a message list — the DSH-native way
 * to detect a user-triggered turn (no heartbeat/cron/sub-agent false positives).
 * Mirrors the heuristic used across host hooks.
 * Handles both array-style content blocks (DSH) and plain string content
 * (OpenAI style) so the hook never misses a user turn due to message shape.
 * @param {Array<object>|undefined} messages
 * @returns {string}
 */
function queryFromMessages(messages) {
  if (!Array.isArray(messages)) return '';
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m?.role !== 'user') continue;
    // Support both array content blocks (DSH) and string content (OpenAI style).
    const blocks = Array.isArray(m.content)
      ? m.content
      : typeof m.content === 'string'
        ? [{ type: 'text', text: m.content }]
        : [];
    const text = blocks.filter((b) => b?.type === 'text').map((b) => b.text).join(' ').trim();
    if (text) return text;
  }
  return '';
}

/**
 * True when the query carries enough signal to be worth a KB search this turn.
 * Filters out short/whitespace/noise prompts cheaply (zero model / I/O).
 * Threshold is symmetric for CJK and Latin: both require ≥2 meaningful units
 * (CJK chars or 3+ letter words) to avoid firing on trivial prompts like "why".
 * @param {string} query
 * @returns {boolean}
 */
function hasMeaningfulQuery(query) {
  let cjk = 0;
  for (const ch of String(query)) {
    const code = ch.codePointAt(0);
    if (code >= 0x4e00 && code <= 0x9fff) cjk += 1;
  }
  const ascii = String(query).toLowerCase().match(/[a-z]{3,}/g);
  return cjk >= 2 || (ascii !== null && ascii.length >= 2);
}
