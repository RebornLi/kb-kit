// ============================================================
// load-probe.mjs — integration smoke test WITHOUT the DSH host.
//
// Stubs the two host shared packages, imports the plugin, and drives
// apply() with a mock ctx to prove the wiring registers:
//   - name === 'kb-context-dsh', inject includes tools + systemPrompt
//   - a `kb_query` tool is registered
//   - an `agent/pre-step` hook is registered and, when fired on a user
//     turn with a real query, injects an extra user message via next()
//   - a system-prompt hint section is registered
//   - heartbeat/cron turns (no user text) are NOT injected
// ============================================================
import { register } from 'node:module';
import { pathToFileURL } from 'node:url';

register('./stub-loader.mjs', import.meta.url);

const plugin = await import('../src/index.js');
const { apply, name, inject } = plugin.default || plugin;
let passed = 0, failed = 0;
const ok = (c, m) => { if (c) { passed += 1; console.log(`  ✅ ${m}`); } else { failed += 1; console.error(`  ❌ ${m}`); } };

ok(name === 'kb-context-dsh', `exported name is kb-context-dsh (got ${name})`);
ok(Array.isArray(inject) && inject.includes('tools') && inject.includes('systemPrompt'), 'inject lists tools + systemPrompt');

// --- mock ctx that records registrations -------------------------------
const hooks = {};
const toolNames = [];
const sections = [];
const ctx = {
  logger: { info: () => {} },
  tools: { register: (t) => toolNames.push(t.name) },
  systemPrompt: { section: (s) => sections.push(s) },
  on: (event, fn) => { hooks[event] = fn; },
};

apply(ctx);
ok(toolNames.includes('kb_query'), `kb_query tool registered (got ${toolNames.join(',')})`);
ok(typeof hooks['agent/pre-step'] === 'function', 'agent/pre-step hook registered');
ok(sections.some((s) => /知识库/.test(s.text)), 'system-prompt hint section registered');
ok(sections.length >= 1, 'at least one system prompt section');

// --- fire the pre-step hook on a NON-user (heartbeat) turn ------------
// decision from next() is what would be entered; hook must NOT add a message.
{
  let nextCalls = 0;
  const out = await hooks['agent/pre-step'](
    { messages: [{ role: 'system', content: [{ type: 'text', text: 'tick' }] }] },
    async () => { nextCalls += 1; return { kind: 'enter', messages: [{ role: 'assistant', content: [{ type: 'text', text: 'hi' }] }] }; },
  );
  ok(nextCalls === 1, 'next() still called for heartbeat (turn proceeds)');
  ok(out.messages.length === 1, 'heartbeat turn gets NO injected KB message');
}

import { queryKB } from '../src/kb.js';
import { loadConfig } from '../src/config.js';

// --- fire on a user turn with a meaningful query ----------------------
// Real vault (default config) → real hits → a fresh user message is
// injected via createUserMessage, and next() still returns 'enter'.
{
  const userText = "知识库 备份 记忆摄取 间隔回忆";
  const probeHits = await queryKB(loadConfig({}), userText);
  const expected = probeHits.length > 0 ? 2 : 1;
  const out = await hooks['agent/pre-step'](
    { agent: { session: {} }, messages: [{ role: 'user', content: [{ type: 'text', text: userText }] }] },
    async () => ({ kind: 'enter', messages: [{ role: 'assistant', content: [{ type: 'text', text: 'ok' }] }] }),
  );
  ok(out.kind === 'enter', 'hook returns a valid enter decision (turn proceeds)');
  ok(out.messages.length === expected, `user turn injects KB message when hits exist (${probeHits.length} hits → ${out.messages.length} msgs)`);
}

// --- dedupe: identical hit-set on the same session is NOT re-injected --
// The host hands us an OBJECT session (WeakMap key); mimic that.
{
  const userText = "知识库 备份 记忆摄取 间隔回忆";
  const sessC = { session: {} }; // same object across both fires = same session
  const first = await hooks['agent/pre-step'](
    { agent: sessC, messages: [{ role: 'user', content: [{ type: 'text', text: userText }] }] },
    async () => ({ kind: 'enter', messages: [{ role: 'assistant', content: [{ type: 'text', text: 'a' }] }] }),
  );
  const second = await hooks['agent/pre-step'](
    { agent: sessC, messages: [{ role: 'user', content: [{ type: 'text', text: userText }] }] },
    async () => ({ kind: 'enter', messages: [{ role: 'assistant', content: [{ type: 'text', text: 'b' }] }] }),
  );
  ok(first.messages.length === 2, `first identical query injects (${first.messages.length} msgs)`);
  ok(second.messages.length === 1, 'second identical query on same session is deduped (prompt-cache)');
}

console.log(`\n# load-probe result: ${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
