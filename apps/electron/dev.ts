// `pnpm dev`: the web bundle's `vite build --watch` and the shell's watch.ts, both children of this process and run directly, so a
// signal reaches them. Ctrl-C, a TERM or HUP, or either child ending stops both; watch.ts also stops, with its Electron, once this
// process is gone (HANDLINE_DEV_PARENT).
import { spawn, type ChildProcess } from 'node:child_process';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '../..');
const bin = path.join(root, 'node_modules/.bin'); // vite and electron, as pnpm would put them on PATH
const env = { ...process.env, PATH: `${bin}${path.delimiter}${process.env.PATH ?? ''}`, HANDLINE_DEV_PARENT: String(process.pid) };
const kids: ChildProcess[] = [
  spawn(path.join(bin, 'vite'), ['build', '--watch'], { cwd: path.join(root, 'apps/web'), stdio: 'inherit', env }),
  spawn(process.execPath, ['watch.ts'], { cwd: import.meta.dirname, stdio: 'inherit', env }),
];
let code = 0;
let left = kids.length;
const stop = (c: number): void => {
  code ||= c;
  for (const k of kids) if (k.exitCode === null && k.signalCode === null) k.kill('SIGTERM');
};
for (const k of kids)
  k.once('exit', (c) => {
    stop(c ?? 1);
    if (--left === 0) process.exit(code);
  });
for (const s of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) process.once(s, () => stop(130));
