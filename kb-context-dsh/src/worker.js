// ============================================================
// worker.js — persistent kb-kit query worker client (DSH side).
//
// The previous per-turn `execFile('python3', ['rag.py','query',...])` re-paid
// interpreter startup + import + ~9.2MB index pickle load (~130ms) on EVERY
// query. This module keeps ONE long-lived `python3 kb_worker.py <root>` child
// per vault: the worker loads the index once and re-loads only when its mtime
// changes. Steady-state queries drop from ~190ms to ~60ms (~3×), and the hook
// stops churning CPU/memory re-parsing the index on every turn.
//
// Safety contract (mirrors the rest of the hook):
//   · lazy spawn — nothing starts until the first query;
//   · if the worker script is missing / fails to spawn / crashes / EOFs, we
//     fall back to the old per-query execFile (self-healing, no regression);
//   · requests are serialized (the worker is a single stdio request/response
//     channel); responses are matched back by a monotonic seq id;
//   · any error resolves to [] — never throws into the agent loop.
// ============================================================
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

// Per-vault worker clients (keyed by kbRoot) so multiple hook instances share one worker.
const clients = new Map();

class WorkerClient {
  constructor(kbRoot, workerPath, ragPath) {
    this.kbRoot = kbRoot;
    this.workerPath = workerPath;
    this.ragPath = ragPath;
    this.child = null;
    this.rl = null;
    this.seq = 0;
    this.pending = new Map(); // seq -> resolve(data)
    this.queue = [];          // serialized request queue
    this.busy = false;
    this.dead = false;        // "no kb_worker.py" → permanent fallback (recreate won't help)
  }

  _ensureSpawned() {
    if (this.child && this.rl) return true;
    if (this.dead) return false;
    if (!existsSync(this.workerPath)) {
      this.dead = true;
      return false; // old vault without kb_worker.py → fall back to execFile
    }
    try {
      this.child = spawn('python3', [this.workerPath, this.kbRoot], {
        cwd: this.kbRoot,
        stdio: ['pipe', 'pipe', 'inherit'],
        env: { ...process.env, PYTHONUNBUFFERED: '1' },
      });
    } catch {
      this.dead = true;
      return false;
    }
    this.rl = createInterface({ input: this.child.stdout });
    this.rl.on('line', (line) => this._onLine(line));
    this.child.on('error', () => this._teardown());
    this.child.on('exit', () => this._teardown());
    return true;
  }

  _onLine(line) {
    let data;
    try {
      data = JSON.parse(line);
    } catch {
      return;
    }
    // 响应按序回，但用 seq 精确匹配；兼容**不回显 seq 的旧 worker**（退化为 FIFO：
    // 取最老的在途请求）。否则旧 worker 的响应因 seq=undefined 永远匹配不上 → 挂死。
    let key = data?.seq;
    if (key === undefined || !this.pending.has(key)) {
      key = this.pending.keys().next().value;
    }
    const resolve = this.pending.get(key);
    if (!resolve) return;
    this.pending.delete(key);
    resolve(data);
  }

  _teardown() {
    this.dead = true;
    // Reject any in-flight requests (caller falls back to execFile).
    for (const resolve of this.pending.values()) resolve({ ok: false, error: 'worker exited' });
    this.pending.clear();
    if (this.rl) { try { this.rl.close(); } catch { /* noop */ } }
    this.rl = null;
    if (this.child) { try { this.child.kill(); } catch { /* noop */ } }
    this.child = null;
  }

  /**
   * Send one query, return { ok, hits } or { ok:false }. Never throws.
   * @param {string} queryText
   * @param {number} topN
   */
  query(queryText, topN, context = 240) {
    return new Promise((resolve) => {
      this.queue.push({ query: queryText, topN, context, resolve });
      this._drain();
    });
  }

  async _drain() {
    if (this.busy) return;
    this.busy = true;
    try {
      while (this.queue.length) {
        const { query, topN, context, resolve } = this.queue.shift();
        if (!this._ensureSpawned()) {
          // Fall back to per-query execFile (old vault / worker unavailable).
          resolve(await this._execFallback(query, topN, context));
          continue;
        }
        const seq = ++this.seq;
        const result = await new Promise((r) => {
          this.pending.set(seq, r);
          try {
            this.child.stdin.write(JSON.stringify({ seq, query, topN, context }) + '\n');
          } catch {
            this._teardown();
            r({ ok: false, error: 'write failed' });
          }
        });
        if (result && result.ok) {
          resolve(result);
        } else {
          // Worker reported failure / died → fall back once for this query.
          this._teardown();
          resolve(await this._execFallback(query, topN, context));
        }
      }
    } finally {
      this.busy = false;
    }
  }

  async _execFallback(queryText, topN, context = 240) {
    try {
      const { stdout } = await execFileAsync('python3', [
        this.ragPath, 'query', queryText,
        '--top', String(topN), '--root', this.kbRoot,
        '--context', String(context), '--json',
      ], {
        cwd: this.kbRoot,
        timeout: 12000,
        env: { ...process.env, PYTHONUNBUFFERED: '1' },
      });
      const data = JSON.parse(stdout || 'null');
      return { ok: true, hits: Array.isArray(data?.hits) ? data.hits : [], meta: data?.meta || {} };
    } catch {
      return { ok: false, error: 'fallback failed' };
    }
  }
}

/**
 * Get (or create) the worker client for a vault. The worker lives in the vault's
 * pipeline dir (ships with kb-kit ≥ the version that added kb_worker.py).
 * @param {Record<string, any>} cfg
 * @returns {WorkerClient}
 */
export function getWorker(cfg) {
  const workerPath = `${cfg.kbRoot}/pipeline/kb_worker.py`;
  let client = clients.get(cfg.kbRoot);
  if (!client || client.dead) {
    client = new WorkerClient(cfg.kbRoot, workerPath, cfg.rag);
    clients.set(cfg.kbRoot, client);
  }
  return client;
}

/** Best-effort shutdown of all workers (called on plugin teardown). */
export function shutdownWorkers() {
  for (const c of clients.values()) {
    try { c._teardown(); } catch { /* noop */ }
  }
  clients.clear();
}
