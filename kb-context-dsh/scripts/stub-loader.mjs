// ============================================================
// stub-loader.mjs — ESM loader hooks that map the two host-provided
// shared packages to in-memory stubs, so load-probe.mjs can import
// the plugin without the DSH host (or `npm install`).
// The runtime shape matches the host contracts:
//   @deepseek-ai/dsh-llm  → createUserMessage(obj) -> obj
//   @deepseek-ai/dsh-tools → defineTool(obj) -> obj
// ============================================================
export async function resolve(spec, ctx, next) {
  if (spec === '@deepseek-ai/dsh-llm') return { url: 'stub:dsh-llm', shortCircuit: true };
  if (spec === '@deepseek-ai/dsh-tools') return { url: 'stub:dsh-tools', shortCircuit: true };
  return next(spec, ctx);
}

export async function load(url, ctx, next) {
  if (url === 'stub:dsh-llm') {
    return {
      format: 'module',
      shortCircuit: true,
      source: `export function createUserMessage(o){ return o; } export default { createUserMessage };`,
    };
  }
  if (url === 'stub:dsh-tools') {
    return {
      format: 'module',
      shortCircuit: true,
      source: `export function defineTool(o){ return o; } export default { defineTool };`,
    };
  }
  return next(url, ctx);
}
