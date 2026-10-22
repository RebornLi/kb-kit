// ============================================================
// kb.js — query the kb-kit vault and normalize hits.
//
// Shells out to kb-kit's rag.py (pure Python stdlib, zero-dependency) with
// `query ... --json` and parses the documented output shape:
//   { "query", "hits": [ {path, title, domain, tags, score, snippet, importance} ], "meta" }
//
// Failure contract: ANY error (missing source, index not built, timeout, bad
// JSON) resolves to an empty array — the hook layer treats that as "skip",
// never as "fail loudly". The model reply is never blocked.
// ============================================================
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

import { getWorker } from './worker.js';

const execFileAsync = promisify(execFile);

/**
 * Run a KB search and return filtered/scored hits only.
 *
 * Fast path: a long-lived `python3 kb_worker.py` child (index loaded once,
 * re-loaded on mtime change) — cuts steady-state per-query from ~190ms to ~60ms.
 * Fallback: if the worker is unavailable (old vault / crash), transparently
 * fall back to the original per-query `rag.py query` execFile (identical result).
 *
 * @param {Record<string, any>} cfg  — from loadConfig()
 * @param {string} queryText          — the user's turn text to search
 * @param {{topN?: number, contextChars?: number}} [opts]
 * @returns {Promise<Array<object>>}   — normalized hit objects, highest score first
 */
export async function queryKB(cfg, queryText, opts = {}) {
  const topN = Number.isInteger(opts.topN) && opts.topN > 0 ? opts.topN : cfg.topN;
  // `--context` widens the snippet the vault returns; "full note" reads ask for a
  // big window (bounded by KB_CONTENT_MAX when normalizing).
  const contextChars = Number.isInteger(opts.contextChars) && opts.contextChars > 0
    ? opts.contextChars
    : cfg.snippetMax;
  if (cfg.kbRoot) {
    const res = await getWorker(cfg).query(queryText, topN, contextChars);
    if (res?.ok) {
      return selectHits(Array.isArray(res.hits) ? res.hits : [], cfg, contextChars);
    }
    // Worker unavailable / failed → fall through to the legacy execFile path.
  }
  return queryKBFallback(cfg, queryText, topN, contextChars);
}

/** Original per-query path (worker-less). Kept as the fallback / reference. */
export async function queryKBFallback(cfg, queryText, topN, contextChars) {
  const args = [
    cfg.rag,
    'query',
    queryText,
    '--top', String(topN),
    '--root', cfg.kbRoot,
    '--context', String(contextChars),
    '--json',
    '--no-record',
  ];

  let stdout;
  try {
    ({ stdout } = await execFileAsync('python3', args, {
      cwd: cfg.kbRoot,
      timeout: cfg.queryTimeoutMs,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    }));
  } catch {
    return []; // missing source / timeout / exec error → empty
  }

  let data;
  try {
    data = JSON.parse(stdout || 'null');
  } catch {
    return []; // non-JSON stdout (warnings + text) → empty
  }

  return selectHits(Array.isArray(data?.hits) ? data.hits : [], cfg, contextChars);
}

/**
 * Fetch the ORIGINAL memory text behind a note (kb-kit `kb raw show`).
 * This is the "必要时调用原记忆文件" path: curated canon is compact, and when the
 * agent genuinely needs the primary source it can pull it verbatim.
 * @param {Record<string, any>} cfg
 * @param {string} rawId  — a `memory_source` ID or a vault-relative path
 * @param {{maxChars?: number, lines?: number}} [opts]
 * @returns {Promise<{ok: boolean, id: string, content: string, truncated?: boolean, note?: string}>}
 */
export async function showRawKB(cfg, rawId, opts = {}) {
  const id = String(rawId ?? '').trim();
  if (!id) return { ok: false, id: '', content: '', note: 'empty id' };
  const maxChars = Number.isInteger(opts.maxChars) && opts.maxChars > 0
    ? opts.maxChars : (cfg.contentMax ?? 6000);
  const script = cfg.raw || `${cfg.kbRoot}/pipeline/kb_raw.py`;
  const args = [script, 'show', id, '--root', cfg.kbRoot, '--full', '--json'];

  let stdout;
  try {
    ({ stdout } = await execFileAsync('python3', args, {
      cwd: cfg.kbRoot,
      timeout: cfg.queryTimeoutMs,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    }));
  } catch (e) {
    return { ok: false, id, content: '', note: String(e?.message || e).slice(0, 200) };
  }
  let data;
  try {
    data = JSON.parse(stdout || 'null');
  } catch {
    return { ok: false, id, content: '', note: 'non-JSON output from kb_raw.py' };
  }
  if (!data || data.ok !== true) {
    // `candidates` must be OMITTED (not set to undefined) when absent: the host
    // snapshots tool output with lossless-JSON rules, and an own key whose value
    // is `undefined` fails that snapshot (`value is not lossless JSON`).
    const out = { ok: false, id, content: '', note: 'not found' };
    if (Array.isArray(data?.candidates) && data.candidates.length) {
      out.candidates = data.candidates.slice(0, 10);
    }
    return out;
  }
  const text = String(data.content || '');
  // Same rule as above: build the result without `undefined`-valued own keys.
  const out = { ok: text.length > 0, id, content: text.slice(0, maxChars), truncated: text.length > maxChars };
  if (typeof data.path === 'string' && data.path) out.path = data.path;
  if (typeof data.chars === 'number' && Number.isFinite(data.chars)) out.chars = data.chars;
  if (Array.isArray(data.copies) && data.copies.length) out.copies = data.copies;
  return out;
}

/**
 * Pure selection pipeline: score filter → min-score → session-dump exclusion →
 * sort desc → cap → normalize. Split out from queryKB so it is unit-testable
 * without shelling out to rag.py.
 * @param {Array<object>} raw
 * @param {Record<string, any>} cfg
 * @returns {Array<object>}
 */
export function selectHits(raw, cfg, snippetMaxOverride) {
  const snippetMax = Number.isInteger(snippetMaxOverride) && snippetMaxOverride > 0
    ? snippetMaxOverride
    : (cfg.snippetMax ?? 40);
  return raw
    .filter((h) => typeof h?.score === 'number')
    .filter((h) => h.score >= cfg.minScore)
    .filter((h) => !cfg.excludeRe.test(h.path || '') && !cfg.excludeRe.test(h.title || ''))
    .sort((a, b) => b.score - a.score)
    .slice(0, cfg.topN)
    .map((h) => normalizeHit(h, snippetMax));
}

/**
 * Drop hits whose path/title is a raw session dump (non-knowledge noise) so
 * we never inject a conversation transcript as if it were reference material.
 * @param {object} h
 * @param {number} snippetMax  — max chars to keep from the snippet
 * @returns {object}
 */
function normalizeHit(h, snippetMax = 40) {
  return {
    path: h.path || h.title || '（无路径）',
    title: h.title || h.path || '（无标题）',
    domain: h.domain || '',
    score: h.score,
    importance: typeof h.importance === 'number' ? h.importance : null,
    snippet: String(h.context || h.snippet || '').replace(/\s+/g, ' ').trim().slice(0, snippetMax),
  };
}
