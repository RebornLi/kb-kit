// ============================================================
// test.mjs — 纯 node 单测（无 openclaw 依赖）
//   node scripts/test.mjs
// 覆盖 config.mjs（配置合并/兜底）与 inject.mjs（注入块/去重键）。
// ============================================================
import assert from "node:assert/strict";
import { loadCfg, DEFAULTS, autoDetectKbRoot } from "../config.mjs";
import { buildInjectedText, hitsKey } from "../inject.mjs";

console.log("# config.mjs");

// 1) 默认值
const c0 = loadCfg({}, {});
assert.equal(c0.topN, DEFAULTS.topN);
assert.equal(c0.minScore, DEFAULTS.minScore);
assert.equal(c0.maxBytes, DEFAULTS.maxBytes);
assert.equal(c0.snippetMax, DEFAULTS.snippetMax);
assert.equal(c0.enabled, true);
assert.equal(c0.dedupe, true);
assert.ok(c0.excludeRe instanceof RegExp);

// 2) 宿主 config 覆盖默认值
const c1 = loadCfg({ topN: 5, minScore: 0.12, enabled: false, dedupe: false, prefix: "X" }, {});
assert.equal(c1.topN, 5);
assert.equal(c1.minScore, 0.12);
assert.equal(c1.enabled, false);
assert.equal(c1.dedupe, false);
assert.equal(c1.prefix, "X");

// 3) env 覆盖宿主 config
const c2 = loadCfg({ topN: 5 }, { KB_TOP_N: "7", KB_MIN_SCORE: "0.4" });
assert.equal(c2.topN, 7);
assert.equal(c2.minScore, 0.4);

// 4) 非法 env 被兜底（NaN → 默认，不 wedge）
assert.equal(loadCfg({}, { KB_TOP_N: "abc" }).topN, DEFAULTS.topN);

// 5) env 强制关闭
assert.equal(loadCfg({}, { KB_HOOK_ENABLED: "false" }).enabled, false);
assert.equal(loadCfg({}, { KB_DEDUPES: "off" }).dedupe, false);

// 6) excludePattern → excludeRe 生效
const c4 = loadCfg({ excludePattern: "Dump" }, {});
assert.ok(c4.excludeRe.test("a-Dump-b"));
assert.ok(!c4.excludeRe.test("plain"));

// 7) 非法正则安全失败（匹配一切 → 命中全排除，不抛）
assert.ok(loadCfg({ excludePattern: "(" }, {}).excludeRe.test("anything"));

// 8) 空 kbRoot 触发自动探测，返回值类型稳定
assert.equal(typeof autoDetectKbRoot(), "string");
assert.equal(typeof c0.kbRoot, "string");

console.log("# inject.mjs");

const cfg = loadCfg({}, {});
assert.equal(buildInjectedText([], cfg), "", "空命中 → 空文本");

const hits = [
  { path: "a/a.md", title: "A", domain: "开发", score: 0.8123, importance: 0.9, snippet: "缓存五分钟" },
  { path: "b/b.md", title: "B", domain: "", score: 0.6, importance: null, snippet: "" },
];
const text = buildInjectedText(hits, cfg);
assert.ok(text.startsWith(cfg.prefix), "prefix 在首行");
assert.equal(text.split("\n")[1], "```", "prefix 后是开围栏");
assert.ok(text.includes("A（a/a.md · 开发，分 0.812，importance 0.9）"), "命中格式化（含 domain/importance）");
assert.ok(text.includes("B（b/b.md，分 0.600）"), "命中格式化（无 domain/importance）");
assert.equal((text.match(/```/g) || []).length, 2, "围栏成对（balanced）");
assert.ok(text.trimEnd().endsWith("忽略）"), "以闭围栏 + 免责说明结尾");

// 字节封顶（多命中、长 snippet）
const big = Array.from({ length: 20 }, (_, i) => ({
  path: `p${i}`, title: `t${i}`, domain: "d", score: 0.9, importance: 1, snippet: "x".repeat(200),
}));
const bigText = buildInjectedText(big, { ...cfg, snippetMax: 200 });
assert.ok(Buffer.byteLength(bigText) <= cfg.maxBytes, `byte cap 生效 (${Buffer.byteLength(bigText)} ≤ ${cfg.maxBytes})`);

// hitsKey 顺序无关
assert.equal(hitsKey([hits[0], hits[1]]), hitsKey([hits[1], hits[0]]), "hitsKey 顺序无关");

console.log("ok: config.mjs + inject.mjs（全部断言通过）");
