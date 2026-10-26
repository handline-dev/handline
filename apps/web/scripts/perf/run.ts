// `pnpm perf`: builds the app with React's profiling renderer, runs it in headless Chrome against an in-process fake daemon, and counts
// what React draws in a few everyday scenarios (typing, messages arriving, switching agents, the Tasks page). The counts are checked
// against perf-budget.json; times are printed but not checked, since they depend on the machine.
// Chrome runs headless only: it never opens a window or takes focus.
//   node scripts/perf/run.ts [--no-build] [--write-budget]
import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { extname, join } from 'node:path';
import { FakeDaemon } from '@handline/fake-daemon/daemon';
import { applySeeds } from '@handline/fake-daemon/seeds';
import { serveWs } from '@handline/fake-daemon/serve';
import { build } from 'vite';

const WEB = join(import.meta.dirname, '../..');
const OUT = join(import.meta.dirname, 'out');
const BUDGET = join(WEB, 'perf-budget.json');
const AGENT = 'handline.architect';
const OTHER = 'handline.pc';
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type Result = { commits: number; renders: number; ms: number; maxCommitMs: number; top: { name: string; renders: number; ms: number }[] };
type Budget = { slack: number; scenarios: Record<string, { commits: number; renders: number }> };

/** The fake with every seed, plus a long chat: 300 messages and 300 replies over three days. */
async function fake(): Promise<{ daemon: FakeDaemon; port: number; incoming: (agent: string) => void }> {
  const daemon = new FakeDaemon();
  applySeeds(daemon, 'all');
  const start = Date.now() - 3 * 86_400_000;
  for (let i = 0; i < 300; i++) {
    const at = new Date(start + i * 600_000).toISOString();
    const mine = i % 2 === 0;
    const body = mine ? `Please look at T${5000 + i} and **check** the \`chat\` list. ` : `Done: see #${400 + i}. `;
    daemon.chat.seed(AGENT, {
      from: mine ? 'human' : AGENT,
      to: mine ? AGENT : 'human',
      at,
      body: body + 'A line that wraps. '.repeat(1 + (i % 4)),
    });
    daemon.chat.seedItem(AGENT, {
      at,
      kind: 'reply',
      text: `Reply ${i} with *markdown* and a [link](https://example.com). `.repeat(1 + (i % 3)),
    });
  }
  const server = serveWs(daemon, 0);
  await new Promise((ok) => server.once('listening', ok));
  let n = 0;
  const incoming = (agent: string) => {
    const id = daemon.chat.seed(agent, { from: agent, to: 'human', body: `Progress ${++n}: \`code\` and T5936.` });
    daemon.emit('chat', 'chat.message', { agent, data: daemon.chat.find(id) as never });
  };
  return { daemon, port: (server.address() as AddressInfo).port, incoming };
}

const TYPES: Record<string, string> = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
};

/** The built app over http, the index page for any path. */
function site(dir: string): Promise<number> {
  const server = http.createServer((req, res) => {
    const path = new URL(req.url ?? '/', 'http://x').pathname;
    let file = join(dir, path === '/' ? 'index.html' : path);
    let body: Buffer;
    try {
      body = readFileSync(file);
    } catch {
      file = join(dir, 'index.html');
      body = readFileSync(file);
    }
    res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' }).end(body);
  });
  return new Promise((ok) => server.listen(0, '127.0.0.1', () => ok((server.address() as AddressInfo).port)));
}

async function measure(page: Page, run: () => Promise<void>): Promise<Result> {
  await page.eval('(__perf.comps = {}, __perf.commits = [], __perf.on = true)');
  await run();
  await sleep(800);
  const r = await page.eval<{ comps: Record<string, { renders: number; ms: number }>; commits: { ms: number; renders: number }[] }>(
    '(__perf.on = false, { comps: __perf.comps, commits: __perf.commits })',
  );
  const round = (n: number) => Math.round(n * 10) / 10;
  return {
    commits: r.commits.length,
    renders: r.commits.reduce((s, c) => s + c.renders, 0),
    ms: round(r.commits.reduce((s, c) => s + c.ms, 0)),
    maxCommitMs: round(Math.max(0, ...r.commits.map((c) => c.ms))),
    top: Object.entries(r.comps)
      .sort((a, b) => b[1].ms - a[1].ms)
      .slice(0, 8)
      .map(([name, c]) => ({ name, renders: c.renders, ms: round(c.ms) })),
  };
}

async function typeInto(page: Page, selector: string, text: string) {
  if (!(await page.eval<boolean>(`!!document.querySelector(${JSON.stringify(selector)})`)))
    throw new Error(`perf: nothing matches ${selector}`);
  await page.eval(`document.querySelector(${JSON.stringify(selector)}).focus()`);
  for (const ch of text) {
    await page.send('Input.insertText', { text: ch });
    await sleep(60);
  }
}

async function scenarios(page: Page, base: string, incoming: (agent: string) => void): Promise<Record<string, Result>> {
  const out: Record<string, Result> = {};
  const go = async (hash: string, ms: number) => (await page.eval(`location.hash = ${JSON.stringify(hash)}`), await sleep(ms));
  await page.send('Page.navigate', { url: `${base}#/agents/${AGENT}` });
  await sleep(6000);
  out['chat: type 30 characters'] = await measure(page, () => typeInto(page, 'textarea', 'please check the chat list now'));
  out['chat: 10 messages arrive in the open chat'] = await measure(page, async () => {
    for (let i = 0; i < 10; i++) {
      incoming(AGENT);
      await sleep(400);
    }
  });
  out['chat: 10 messages arrive for another agent'] = await measure(page, async () => {
    for (let i = 0; i < 10; i++) {
      incoming(OTHER);
      await sleep(400);
    }
  });
  out['switch agent 6 times'] = await measure(page, async () => {
    for (let i = 0; i < 6; i++) await go(`#/agents/${i % 2 ? AGENT : OTHER}`, 1200);
  });
  await go('#/tasks', 3000);
  out['tasks: type 10 characters in search'] = await measure(page, () => typeInto(page, 'input[placeholder="Search"]', 'chat scrol'));
  return out;
}

function check(results: Record<string, Result>, budget: Budget): string[] {
  const over: string[] = [];
  for (const [name, r] of Object.entries(results)) {
    const b = budget.scenarios[name];
    if (!b) {
      over.push(`${name}: no budget (run with --write-budget)`);
      continue;
    }
    for (const k of ['commits', 'renders'] as const)
      if (r[k] > b[k] * (1 + budget.slack)) over.push(`${name}: ${r[k]} ${k}, budget ${b[k]} (+${budget.slack * 100}%)`);
  }
  return over;
}

const CANDIDATES = [
  process.env.CHROME_PATH,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
];

type Page = {
  send: (method: string, params?: Record<string, unknown>) => Promise<Record<string, unknown>>;
  /** Evaluates an expression in the page and returns its value (awaited when it is a promise). */
  eval: <T>(expression: string) => Promise<T>;
  close: () => Promise<void>;
};

async function launch(): Promise<Page> {
  const bin = CANDIDATES.find((c) => c && existsSync(c));
  if (!bin) throw new Error('perf: no Chrome found; set CHROME_PATH');
  const profile = mkdtempSync(join(tmpdir(), 'handline-perf-'));
  const args = ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run', '--window-size=1400,900'];
  if (process.env.CI) args.push('--no-sandbox');
  // A process group of its own, so close() stops Chrome's helpers too and not only the main process.
  const proc = spawn(bin, [...args, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'], detached: true });
  const stop = () => {
    try {
      process.kill(-proc.pid!, 'SIGTERM');
    } catch {
      // already gone
    }
  };
  process.once('exit', stop); // a run that dies on an error leaves no Chrome behind
  const port = await devtoolsPort(proc);
  const pages = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()) as { type: string; webSocketDebuggerUrl: string }[];
  const ws = new WebSocket(pages.find((p) => p.type === 'page')!.webSocketDebuggerUrl);
  await new Promise((ok, fail) => (ws.addEventListener('open', ok), ws.addEventListener('error', fail)));
  let next = 0;
  const waiting = new Map<number, (r: Record<string, unknown>) => void>();
  ws.addEventListener('message', (m) => {
    const d = JSON.parse(String(m.data));
    waiting.get(d.id)?.(d);
    waiting.delete(d.id);
  });
  const send = (method: string, params = {}) =>
    new Promise<Record<string, unknown>>((ok) => {
      waiting.set(++next, ok);
      ws.send(JSON.stringify({ id: next, method, params }));
    });
  return {
    send,
    eval: async <T>(expression: string) => {
      const r = (await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })) as {
        result?: { result?: { value?: T }; exceptionDetails?: { text: string } };
      };
      if (r.result?.exceptionDetails) throw new Error(`perf: page error: ${r.result.exceptionDetails.text}`);
      return r.result?.result?.value as T;
    },
    close: async () => {
      ws.close();
      const gone = new Promise((ok) => proc.once('exit', ok));
      stop();
      await gone;
      // The profile is a temp folder: a helper still writing into it must not fail the check.
      try {
        rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
      } catch (e) {
        console.warn(`perf: left ${profile}: ${(e as Error).message}`);
      }
    },
  };
}

/** Chrome prints the port it picked on stderr. */
function devtoolsPort(proc: ChildProcess): Promise<number> {
  return new Promise((ok, fail) => {
    let text = '';
    const timer = setTimeout(() => fail(new Error(`perf: Chrome did not start:\n${text}`)), 20_000);
    proc.stderr!.on('data', (b: Buffer) => {
      text += b;
      const m = /DevTools listening on ws:\/\/[^:]+:(\d+)\//.exec(text);
      if (!m) return;
      clearTimeout(timer);
      ok(Number(m[1]));
    });
    proc.on('exit', (code) => fail(new Error(`perf: Chrome exited (${code}):\n${text}`)));
  });
}

const argv = process.argv.slice(2);
// React's profiling renderer times each component; unminified so the names read.
if (!argv.includes('--no-build'))
  await build({
    root: WEB,
    configFile: join(WEB, 'vite.config.ts'),
    logLevel: 'error',
    resolve: { alias: [{ find: /^react-dom\/client$/, replacement: 'react-dom/profiling' }] },
    build: { outDir: join(OUT, 'dist'), emptyOutDir: true, minify: false },
  });
if (!existsSync(join(OUT, 'dist/index.html'))) throw new Error('perf: no build to run; run it once without --no-build');
const { daemon, port, incoming } = await fake();
const base = `http://127.0.0.1:${await site(join(OUT, 'dist'))}/?daemon=ws://127.0.0.1:${port}`;
const page = await launch();
let results: Record<string, Result>;
try {
  await page.send('Page.enable');
  await page.send('Page.addScriptToEvaluateOnNewDocument', { source: readFileSync(join(import.meta.dirname, 'hook.js'), 'utf8') });
  results = await scenarios(page, base, incoming);
} finally {
  await page.close();
  daemon.stop();
}
mkdirSync(OUT, { recursive: true });
writeFileSync(join(OUT, 'perf.json'), JSON.stringify(results, null, 1) + '\n');
console.table(
  Object.fromEntries(
    Object.entries(results).map(([k, r]) => [k, { commits: r.commits, renders: r.renders, ms: r.ms, 'max commit ms': r.maxCommitMs }]),
  ),
);
if (argv.includes('--write-budget')) {
  const scenarios = Object.fromEntries(Object.entries(results).map(([k, r]) => [k, { commits: r.commits, renders: r.renders }]));
  writeFileSync(BUDGET, JSON.stringify({ slack: 0.1, scenarios }, null, 2) + '\n');
  console.log(`perf: wrote ${BUDGET}`);
  process.exit(0);
}
const over = check(results, JSON.parse(readFileSync(BUDGET, 'utf8')));
for (const o of over) console.error(`over budget: ${o}`);
process.exit(over.length ? 1 : 0);
