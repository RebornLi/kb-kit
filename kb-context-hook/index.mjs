// ============================================================
// kb-context-hook —— 在每轮模型生成前，自动把 kb-kit 检索命中拼进 prompt
//
// 接入方式：before_prompt_build（Modify 钩子）
//   返回 { appendContext } 把检索片段追加到当轮提示词，无需模型显式调 kb。
//
// 设计纪律：
//   - 只对“用户发起”的回合跑（ctx.trigger === "user"），跳过 heartbeat/cron
//   - kb query 失败 / 超时 / 无命中 → 静默跳过，绝不阻断回复
//   - 注入的是外部内容，用明确边界（围栏）框起来，模型只当参考资料
//   - 同一会话内相同命中集不重复注入（prompt-cache hygiene），可用 KB_DEDUPES 关
//   - 配置来自 openclaw.plugin.json 的 config（api.pluginConfig），env 可覆盖
// ============================================================
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { appendFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadCfg } from "./config.mjs";
import { buildInjectedText, hitsKey } from "./inject.mjs";

const execFileAsync = promisify(execFile);

// 去重 Map 的会话上限，防止长期运行无界增长
const MAX_TRACKED_SESSIONS = 500;

// --- 调试日志:每次触发写一行，便于观察钩子是否真的跑 ---
// 默认落到系统临时目录，绝不写死用户私人路径；可用 KB_HOOK_DEBUG_LOG 覆盖。
const DEBUG_LOG = process.env.KB_HOOK_DEBUG_LOG || join(tmpdir(), "kb-hook-debug.log");
function dbg(msg) {
  try {
    const line = new Date().toISOString() + " \u2192 " + msg + "\n";
    appendFileSync(DEBUG_LOG, line, "utf-8");
  } catch { /* 写日志失败绝不阻断回复 */ }
}

// --- 只统计，供调试日志 ---
let runs = 0, injected = 0, skipped = 0;

/**
 * 调 kb query --json，返回规范化后的命中对象数组。
 * 失败一律返回空数组（不抛），让上层静默跳过。
 * @param {Record<string, any>} cfg
 * @param {string} queryText
 */
async function queryKB(cfg, queryText) {
  const args = [
    cfg.rag, "query", queryText,
    "--top", String(cfg.topN),
    "--root", cfg.kbRoot,
    "--json",
  ];
  const { stdout } = await execFileAsync("python3", args, {
    cwd: cfg.kbRoot,
    timeout: 12000,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });
  const data = JSON.parse(stdout || "null");
  const hits = Array.isArray(data?.hits) ? data.hits : [];
  return hits
    .filter((h) => typeof h?.score === "number")
    .filter((h) => h.score >= cfg.minScore)                  // 高阈值：只引高相关
    .filter((h) => !cfg.excludeRe.test(h.path || "") && !cfg.excludeRe.test(h.title || ""))  // 排除会话转储
    .sort((a, b) => b.score - a.score)
    .slice(0, cfg.topN)
    .map((h) => ({
      path: h.path || h.title || "（无路径）",
      title: h.title || h.path || "（无标题）",
      domain: h.domain || "",
      score: h.score,
      importance: typeof h.importance === "number" ? h.importance : null,
      snippet: String(h.snippet || "").replace(/\s+/g, " ").trim().slice(0, cfg.snippetMax),
    }));
}

export default definePluginEntry({
  id: "kb-context-hook",
  name: "KB Context Hook",
  description: "Automatically inject local kb-kit search hits into every user turn.",
  register(api) {
    // 宿主 config（openclaw.plugin.json configSchema 校验后经 api.pluginConfig 注入）
    // → env → 默认。保留 api.config 兼容旧宿主。
    const CFG = loadCfg(api.pluginConfig ?? api.config, process.env);

    // 总开关关闭，或未探测到 vault → 不注册钩子（零开销）
    if (!CFG.enabled) {
      dbg("skipped: KB_HOOK_ENABLED=false");
      return;
    }
    if (!CFG.kbRoot) {
      dbg("skipped: no kbRoot configured/detected");
      return;
    }

    // per-session 上次注入的命中键（ctx.sessionKey 是字符串，故用 Map 而非 WeakMap）
    const lastKeyBySession = new Map();

    api.on(
      "before_prompt_build",
      async (event, ctx) => {
        // 只对用户发起回合跑：heartbeat/cron/子会话投递一律跳过
        if (ctx?.trigger !== "user") return;

        const prompt = (event?.prompt || "").toString().trim();
        if (!prompt) return;

        runs += 1;
        try {
          const hits = await queryKB(CFG, prompt);
          if (!hits.length) {
            skipped += 1;
            dbg("trigger=user hits=0 skipped");
            return;
          }

          // 同会话内相同命中集不重复注入（prompt-cache hygiene）
          if (CFG.dedupe !== false) {
            const sessKey = ctx?.sessionKey;
            if (typeof sessKey === "string" && sessKey) {
              const key = hitsKey(hits);
              if (lastKeyBySession.get(sessKey) === key) {
                skipped += 1;
                dbg("trigger=user dedupe skipped");
                return;
              }
              lastKeyBySession.set(sessKey, key);
              if (lastKeyBySession.size > MAX_TRACKED_SESSIONS) {
                lastKeyBySession.delete(lastKeyBySession.keys().next().value);
              }
            }
          }

          const context = buildInjectedText(hits, CFG);
          if (!context) {
            skipped += 1;
            dbg("trigger=user empty-context skipped");
            return;
          }
          injected += 1;
          dbg(`trigger=user hits=${hits.length} injected=1 title=${hits[0]?.title ?? hits[0]?.path ?? "-"}`);
          return { appendContext: context };
        } catch (err) {
          // 检索失败/超时/解析错误 → 静默跳过，绝不阻断回复
          skipped += 1;
          dbg(`trigger=user error=${String(err).split("\n")[0].slice(0,120)} skipped`);
          try { api.emit?.("debug", { plugin: "kb-context-hook", err: String(err) }); } catch { /* noop */ }
        }
      },
      {
        // 让钩子跟当前回合的工具策略一致（如需走工具通道调 kb）
        // timeoutMs 覆盖在 config.hooks.timeoutMs
      },
    );
  },
});
