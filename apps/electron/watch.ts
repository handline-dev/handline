// `pnpm app:watch`: rebuilds the main process and preloads on every save and restarts Electron after each rebuild.
// The web bundle is rebuilt by `vite build --watch` (run beside this); reload the window (Cmd+R) to see it.
import { spawn, type ChildProcess } from 'node:child_process';
import { context, type BuildOptions } from 'esbuild';

let child: ChildProcess | undefined;
const restart = (): void => {
  const old = child;
  const start = (): void => {
    child = spawn('electron', ['.'], { stdio: 'inherit', shell: false });
  };
  if (old && old.exitCode === null) {
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
  plugins: [{ name: 'restart', setup: (b) => b.onEnd((r) => r.errors.length === 0 && restart()) }],
};

const ctx = await context({
  ...common,
  entryPoints: { main: 'src/main.ts', preload: 'src/preload.ts', 'popover-preload': 'src/popover-preload.ts' },
  outdir: 'dist',
  outExtension: { '.js': '.cjs' },
});
await ctx.watch();
process.on('SIGINT', () => {
  child?.kill();
  process.exit(0);
});
