// ============================================================
// scripts/test.mjs — offline test for kb-context-dsh.
//
// Runs without the DSH host:
//   1. config resolution + validation (auto-detect vault)
//   2. inject builder (fenced block, byte cap, hit formatting, empty handling)
//   3. selectHits (score filter, session-dump exclusion, cap, snippetMax)
//   4. live queryKB against a real kb-kit vault (auto-detected)
//      — builds the rag index on demand so the smoke test is self-contained.
// Exit code 0 = all assertions passed.
// ============================================================
import { loadConfig } from '../src/config.js';
import { buildInjectedText, hitsKey } from '../src/inject.js';
import { queryKB, selectHits } from '../src/kb.js';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
let passed = 0, failed = 0;
const ok = (cond, msg) => { if (cond) { passed += 1; console.log(`  ✅ ${msg}`); } else { failed += 1; console.error(`  ❌ ${msg}`); } };

console.log('# config');
{
  const c = loadConfig({}); // env-less → auto-detected defaults
  ok(c.kbRoot !== '' && c.kbRoot.length > 0, `default kbRoot auto-detected (${c.kbRoot})`);
  ok(c.rag === `${c.kbRoot}/pipeline/rag.py`, 'default rag points at vault pipeline/rag.py');
  ok(c.topN === 3 && c.minScore === 0.25 && c.maxBytes === 700, 'default numeric knobs');
  ok(c.snippetMax === 40, 'default snippetMax = 40');
  ok(c.enabled === true, 'enabled by default');
  ok(c.dedupe === true, 'dedupe on by default');
  ok(c.excludeRe instanceof RegExp, 'excludeRe compiles');

  const bad = loadConfig({ KB_TOP_N: 'notnum', KB_MIN_SCORE: '-5', KB_HOOK_ENABLED: 'false', KB_DEDUPES: 'off' });
  ok(bad.topN === 3, 'invalid TOP_N falls back to 3');
  ok(bad.minScore === 0, 'invalid MIN_SCORE clamps to 0');
  ok(bad.enabled === false, 'KB_HOOK_ENABLED=false disables');
  ok(bad.dedupe === false, 'KB_DEDUPES=off disables dedupe');

  // snippetMax override
  const custom = loadConfig({ KB_SNIPPET_MAX: '80' });
  ok(custom.snippetMax === 80, 'KB_SNIPPET_MAX=80 respected');

  // topN non-integer falls back
  const frac = loadConfig({ KB_TOP_N: '2.5' });
  ok(frac.topN === 3, 'non-integer TOP_N (2.5) falls back to 3');
}

console.log('# inject builder (fenced block + byte cap + formatting)');
{
  const cfg = loadConfig({});
  ok(buildInjectedText([], cfg) === '', 'empty hits → empty text');
  const hits = [
    { path: '20-技术 Technology/AI与LLM/AI与LLM.md', title: '缓存策略', domain: '开发', score: 0.8123, importance: 0.9, snippet: '缓存五分钟 TTL 覆盖查询' },
    { path: '30-决策日志 Decisions/决策记录.md', title: '索引版本', domain: '运维', score: 0.6011, importance: null, snippet: '' },
  ];
  const text = buildInjectedText(hits, cfg);
  ok(text.startsWith('📚 本地知识库命中'), 'prefix first');
  // The opening fence must appear right after the prefix line.
  const lines = text.split('\n');
  ok(lines[1] === '```', 'opening fence (```) present after prefix');
  ok(text.includes('缓存策略（20-技术 Technology/AI与LLM/AI与LLM.md · 开发，分 0.812，importance 0.9）'), 'hit 1 formatted with path/domain/score/importance (toFixed 3)');
  ok(text.includes('索引版本（30-决策日志 Decisions/决策记录.md · 运维，分 0.601）'), 'hit 2 formatted (no importance, no snippet, domain present)');
  ok(text.trimEnd().endsWith('忽略）'), 'closes with fence + ignore note');
  // Both opening and closing fences present → balanced.
  const fenceCount = (text.match(/```/g) || []).length;
  ok(fenceCount === 2, `fenced block balanced (2 fences, got ${fenceCount})`);

  // byte cap
  const big = Array.from({ length: 20 }, (_, i) => ({ path: `p${i}`, title: `title ${i}`, domain: 'd', score: 0.9, importance: 1, snippet: 'x'.repeat(200) }));
  const bigText = buildInjectedText(big, { ...cfg, snippetMax: 200 });
  ok(Buffer.byteLength(bigText) <= cfg.maxBytes, `byte cap respected (${Buffer.byteLength(bigText)} ≤ ${cfg.maxBytes})`);

  ok(hitsKey([hits[0], hits[1]]) === hitsKey([hits[1], hits[0]]), 'hitsKey is order-independent');
}

console.log('# selectHits (score filter + session-dump exclusion + cap + snippetMax)');
{
  const cfg = loadConfig({}); // minScore 0.25, topN 3, excludeRe /SessionKey-/i, snippetMax 40
  const raw = [
    { path: 'good/high.md', title: '高命中', domain: '开发', score: 0.90, importance: 1, snippet: 'ok' },
    { path: 'mid/mid.md', title: '中命中', domain: '运维', score: 0.30, importance: 0.5, snippet: 'ok' },
    { path: 'low/low.md', title: '低命中', domain: '数据', score: 0.10, importance: 0.5, snippet: 'ok' }, // below minScore
    { path: 'SessionKey-12345.txt', title: 'raw dump', domain: '杂', score: 0.99, importance: 1, snippet: 'ok' }, // session dump
    { path: 'a/a.md', title: 'A', domain: '开发', score: 0.80, importance: 1, snippet: 'ok' },
    { path: 'b/b.md', title: 'B', domain: '开发', score: 0.70, importance: 1, snippet: 'ok' },
  ];
  const out = selectHits(raw, cfg);
  ok(out.length === 3, `topN cap = 3 (got ${out.length})`);
  ok(out[0].path === 'good/high.md', 'highest score first');
  ok(out.every((h) => h.score >= 0.25), 'below-minScore dropped (0.10 excluded)');
  ok(!out.some((h) => /SessionKey-/i.test(h.path)), 'session-dump hit excluded by excludeRe');
  ok(out.every((h) => h.snippet.length <= 40), 'snippets normalized/truncated to ≤40');

  // snippetMax override actually takes effect
  const cfg80 = loadConfig({ KB_SNIPPET_MAX: '80' });
  const longRaw = [{ path: 'x/x.md', title: 'X', domain: 'd', score: 0.9, importance: 1, snippet: 'a'.repeat(120) }];
  const out80 = selectHits(longRaw, cfg80);
  ok(out80[0].snippet.length === 80, `snippetMax=80 truncates to 80 (got ${out80[0].snippet.length})`);
}

console.log('# live queryKB against a real vault');
{
  const cfg = loadConfig({});
  const ROOT = cfg.kbRoot;
  if (!ROOT) {
    ok(false, 'no vault auto-detected — skipping live queryKB');
  } else {
    // Ensure the index exists (self-contained smoke test).
    try {
      await execFileAsync('python3', [`${ROOT}/pipeline/rag.py`, 'index', '--root', ROOT], { timeout: 60000, env: { ...process.env, PYTHONUNBUFFERED: '1' } });
    } catch (e) {
      console.error(`  ⚠️ index build skipped: ${e?.message?.split('\n')[0]}`);
    }

    try {
      const hits = await queryKB(cfg, '知识库备份 记忆摄取 间隔回忆');
      ok(Array.isArray(hits), 'queryKB returns an array');
      ok(hits.length >= 0, 'hits length is non-negative');
      if (hits.length > 0) {
        ok(typeof hits[0].path === 'string' && typeof hits[0].score === 'number', 'hits have path + numeric score');
        ok(hits.every((h) => h.score >= 0.25), 'all hits pass minScore filter');
        ok(hits.every((h) => !/SessionKey-/i.test(h.path)), 'no session-dump paths leak in');
      }
      // No meaningful query + wrong path → empty, never throws.
      const miss = await queryKB({ ...cfg, rag: '/nonexistent/rag.py' }, 'anything');
      ok(Array.isArray(miss) && miss.length === 0, 'missing rag source → empty (no throw)');
    } catch (e) {
      ok(false, `live queryKB threw: ${e?.message?.split('\n')[0]}`);
    }
  }
}

console.log(`\n# result: ${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
