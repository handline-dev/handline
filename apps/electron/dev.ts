// `pnpm dev`: the web bundle's vite build --watch, run in this process, and the shell's watch.ts as a child on an IPC channel. Ctrl-C,
// a TERM or HUP, or watch.ts ending stops both; watch.ts quits with its Electron the moment this process is gone, even by kill -9,
// because its channel disconnects.
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import type * as Vite from 'vite';

const root = path.resolve(import.meta.dirname, '../..');
const web = path.join(root, 'apps/web');
// vite is apps/web's own dependency, so it is loaded from there.
const vite = (await import(pathToFileURL(createRequire(path.join(web, 'package.json')).resolve('vite')).href)) as typeof Vite;
const bundle = await vite.build({ root: web, configFile: path.join(web, 'vite.config.ts'), build: { watch: {} } });
const bin = path.join(root, 'node_modules/.bin'); // electron, as pnpm would put it on PATH
const shell = spawn(process.execPath, ['watch.ts'], {
  cwd: import.meta.dirname,
  stdio: ['inherit', 'inherit', 'inherit', 'ipc'],
  env: { ...process.env, PATH: `${bin}${path.delimiter}${process.env.PATH ?? ''}` },
});
const stop = async (code: number): Promise<never> => {
  if (shell.exitCode === null && shell.signalCode === null) shell.kill('SIGTERM');
  if ('close' in bundle) await bundle.close();
  process.exit(code);
};
shell.once('exit', (c) => void stop(c ?? 1));
for (const s of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) process.once(s, () => void stop(130));
