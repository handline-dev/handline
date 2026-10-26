// Makes the installable app (`pnpm package`): the web build, the main process, the universal daemon and CLI, then electron-builder.
// The version is the root of the build: `pnpm package 0.1.0` stamps it, `pnpm package` says dev.
import { spawnSync } from 'node:child_process';
import path from 'node:path';

const here = import.meta.dirname;
const top = path.resolve(here, '../..');
const version = process.argv[2] ?? 'dev';

function run(cmd: string, args: string[], cwd = top): void {
  const r = spawnSync(cmd, args, { cwd, stdio: 'inherit' });
  if (r.status !== 0) process.exit(r.status ?? 1);
}

run('pnpm', ['--filter', '@handline/web', 'build']);
run('node', ['build.ts'], here);
run('bash', ['tools/build-darwin.sh', version]);
run(
  'pnpm',
  [
    'exec',
    'electron-builder',
    '--mac',
    '--config',
    'electron-builder.config.mjs',
    `-c.extraMetadata.version=${version === 'dev' ? '0.0.0-dev' : version}`,
  ],
  here,
);
