// `pnpm dev`: rebuilds the main process and preloads on every save and restarts Electron after each rebuild.
// The web bundle is rebuilt by `vite build --watch` (run beside this); reload the window (Cmd+R) to see it.
// One runs per checkout (dist/.watch.pid); it stops with its Electron when dev.ts's channel disconnects or whoever started it ends.
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { context, type BuildOptions } from 'esbuild';

const webDist = process.env.HANDLINE_WEB_DIST ?? process.env.OCP_WEB_DIST ?? path.resolve(import.meta.dirname, '../web/dist');
const webIndex = path.join(webDist, 'index.html');
const goDir = path.resolve(import.meta.dirname, '../../go');
const alive = (pid: number): boolean => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};
const lock = path.resolve(import.meta.dirname, 'dist/.watch.pid');
const holder = Number(fs.existsSync(lock) ? fs.readFileSync(lock, 'utf8') : 0);
if (holder && holder !== process.pid && alive(holder)) {
  console.error(`pnpm dev already runs in this checkout (pid ${holder}): stop it first`);
  process.exit(1);
}
fs.mkdirSync(path.dirname(lock), { recursive: true });
fs.writeFileSync(lock, String(process.pid));
// Electron's own binary, not its CLI wrapper: a kill that lands while the wrapper starts would leave the app running without it.
const electron = createRequire(import.meta.url)('electron') as string;
let child: ChildProcess | undefined;
let stopping = false;
let quitting = false;
const restart = (): void => {
  if (stopping) return; // the exit already in flight starts Electron once, from the latest build
  const old = child;
  const start = (): void => {
    if (quitting) return;
    stopping = false;
    child = spawn(electron, ['.'], { stdio: 'inherit', shell: false });
  };
  if (old && old.exitCode === null && old.signalCode === null) {
    stopping = true;
    old.once('exit', start);
    old.kill();
  } else start();
};

// One restart for a burst of rebuilds: a save can end several builds within a moment.
let soon: NodeJS.Timeout | undefined;
const restartSoon = (): void => {
  clearTimeout(soon);
  soon = setTimeout(restart, 300);
};

const common: BuildOptions = {
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node22',
  external: ['electron'],
  logLevel: 'info',
  define: { 'import.meta.dirname': '__dirname', 'import.meta.filename': '__filename' },
  plugins: [
    {
      name: 'restart',
      setup: (b) =>
        b.onEnd((r) => {
          if (r.errors.length === 0 && child) restartSoon();
        }),
    },
  ],
};

// Own mode starts go/handlined; a fresh checkout has none.
if (fs.existsSync(goDir) && !fs.existsSync(path.join(goDir, 'handlined')))
  spawnSync('go', ['build', '-o', 'handlined', './cmd/handlined'], { cwd: goDir, stdio: 'inherit' });

const ctx = await context({
  ...common,
  entryPoints: { main: 'src/main.ts', preload: 'src/preload.ts', 'popover-preload': 'src/popover-preload.ts' },
  outdir: 'dist',
  outExtension: { '.js': '.cjs' },
});
await ctx.watch();
// Electron serves apps/web/dist, so the first start waits for the first vite build.
while (!fs.existsSync(webIndex)) await sleep(250);
restart();
// Electron is waited for, so it never outlives this process; one that has not quit within 5 s is killed.
const quit = (): void => {
  clearTimeout(soon);
  quitting = true;
  if (fs.existsSync(lock) && fs.readFileSync(lock, 'utf8') === String(process.pid)) fs.rmSync(lock);
  if (!child || child.exitCode !== null || child.signalCode !== null) process.exit(0);
  child.once('exit', () => process.exit(0));
  child.kill();
  setTimeout(() => child?.kill('SIGKILL'), 5000).unref();
};
for (const s of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) process.on(s, quit);
process.once('disconnect', quit);
// Started from a shell without dev.ts there is no channel to disconnect: when whoever started this ends, it ends with its Electron.
const starter = process.ppid;
setInterval(() => process.ppid !== starter && quit(), 5000).unref();
