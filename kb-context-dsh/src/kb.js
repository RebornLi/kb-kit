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

const execFileAsync = promisify(execFile);

/**
 * Run a KB search and return filtered/scored hits only.
 * @param {Record<string, any>} cfg  — from loadConfig()
 * @param {string} queryText          — the user's turn text to search
 * @returns {Promise<Array<object>>}   — normalized hit objects, highest score first
 */
export async function queryKB(cfg, queryText) {
  const args = [
    cfg.rag,
    'query',
    queryText,
    '--top', String(cfg.topN),
    '--root', cfg.kbRoot,
    '--json',
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

  return selectHits(Array.isArray(data?.hits) ? data.hits : [], cfg);
}

/**
 * Pure selection pipeline: score filter → min-score → session-dump exclusion →
 * sort desc → cap → normalize. Split out from queryKB so it is unit-testable
 * without shelling out to rag.py.
 * @param {Array<object>} raw
 * @param {Record<string, any>} cfg
 * @returns {Array<object>}
 */
export function selectHits(raw, cfg) {
  const snippetMax = cfg.snippetMax ?? 40;
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
    snippet: String(h.snippet || '').replace(/\s+/g, ' ').trim().slice(0, snippetMax),
  };
}
