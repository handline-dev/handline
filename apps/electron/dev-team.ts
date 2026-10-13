// `pnpm dev:team <team>`: the vite page and the shell against the team's dev instance (src/dev-team.ts). An instance that is off is
// said and the run stops; there is no fallback to the live daemon.
import { spawn, type ChildProcess } from 'node:child_process';
import os from 'node:os';
import { answers, freePort, instanceFile, offReason, readInstance, teamEnv } from './src/dev-team.ts';

const team = process.argv[2];
if (!team) {
  console.error('usage: pnpm dev:team <team>');
  process.exit(2);
}
const i = readInstance(instanceFile(os.homedir(), team), team);
const why = typeof i === 'string' ? i : await offReason(i);
if (typeof i === 'string' || why) {
  console.error(typeof i === 'string' ? i : why);
  process.exit(1);
}
const port = await freePort();
const devUrl = `http://127.0.0.1:${port}`;
const env = teamEnv(process.env, i, devUrl);
console.log(`${team}: ${i.branch}@${i.sha}, gateway ${i.gateway}, page ${devUrl}`);
const vite = spawn('pnpm', ['--filter', '@handline/web', 'dev', '--port', String(port), '--strictPort'], { stdio: 'inherit', env });
const stop = (code: number) => {
  vite.kill();
  process.exit(code);
};
vite.once('exit', () => stop(1));
for (let n = 0; !(await answers(`127.0.0.1:${port}`)); n++) {
  if (n > 120) stop(1);
  await new Promise((r) => setTimeout(r, 250));
}
const shell: ChildProcess = spawn('pnpm', ['--filter', '@handline/electron', 'start'], { stdio: 'inherit', env });
shell.once('exit', (code) => stop(code ?? 0));
process.once('SIGINT', () => (shell.kill(), stop(130)));
