// `pnpm app:watch`: rebuilds the main process and preloads on every save and restarts Electron after each rebuild.
// The web bundle is rebuilt by `vite build --watch` (run beside this); reload the window (Cmd+R) to see it.
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { context, type BuildOptions } from 'esbuild';

const webDist = process.env.HANDLINE_WEB_DIST ?? process.env.OCP_WEB_DIST ?? path.resolve(import.meta.dirname, '../web/dist');
const webIndex = path.join(webDist, 'index.html');
const goDir = path.resolve(import.meta.dirname, '../../go');
let child: ChildProcess | undefined;
let stopping = false;
const restart = (): void => {
  if (stopping) return; // the exit already in flight starts Electron once, from the latest build
  const old = child;
  const start = (): void => {
    stopping = false;
    child = spawn('electron', ['.'], { stdio: 'inherit', shell: false });
  };
  if (old && old.exitCode === null && old.signalCode === null) {
    stopping = true;
    old.once('exit', start);
    old.kill();
  } else start();
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
          if (r.errors.length === 0 && child) restart();
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
process.on('SIGINT', () => {
  child?.kill();
  process.exit(0);
});
