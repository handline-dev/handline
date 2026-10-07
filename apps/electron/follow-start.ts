// `pnpm app:main`: runs Handline from its own checkout of main, which the app then keeps offering to update (src/follow.ts).
// The checkout is a clone of this repo's own main (the merger lands there), kept apart from every working tree, so nobody's work is touched.
import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const run = (cwd: string, file: string, args: string[]): void => {
  const r = spawnSync(file, args, { cwd, stdio: 'inherit' });
  if (r.status !== 0) {
    console.error(`${file} ${args.join(' ')} failed`);
    process.exit(r.status ?? 1);
  }
};

const gitDir = execFileSync('git', ['rev-parse', '--path-format=absolute', '--git-common-dir'], { encoding: 'utf8' }).trim();
const dir = process.env.HANDLINE_FOLLOW_DIR ?? path.join(os.homedir(), '.local', 'share', 'handline', 'app-main');

if (!fs.existsSync(path.join(dir, '.git'))) {
  fs.mkdirSync(path.dirname(dir), { recursive: true });
  run(process.cwd(), 'git', ['clone', '--branch', 'main', '--single-branch', gitDir, dir]);
} else {
  run(dir, 'git', ['fetch', 'origin', 'main']);
  run(dir, 'git', ['merge', '--ff-only', 'origin/main']);
}
run(dir, 'pnpm', ['install', '--frozen-lockfile']);
if (fs.existsSync(path.join(dir, 'go')) && spawnSync('go', ['version']).status === 0)
  run(dir, 'go', ['build', '-C', 'go', '-o', 'handlined', './cmd/handlined']);
run(dir, 'pnpm', ['--filter', '@handline/web', 'build']);
process.env.HANDLINE_FOLLOW = '1';
process.env.HANDLINE_FOLLOW_DIR = dir;
run(dir, 'pnpm', ['--filter', '@handline/electron', 'start']);
