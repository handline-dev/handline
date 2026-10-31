// The Electron side of upgrading the daemon of a checkout run (pnpm app): its build step, its binary, and the calls on the shell's own connection.
// An installed app does not come here: its `service ensure` moves the daemon (service.ts).
import { execFile } from 'node:child_process';
import path from 'node:path';
import type { DaemonClient } from '@handline/client';
import { readBuildSha } from '#src/build-id.ts';
import { upgradeDaemon } from '#src/upgrade.ts';

/**
 * Moves the running daemon onto this app's own handlined and waits for it. `sha` is the build to move to (the app's, read now: an update
 * has just built a newer one than the app runs); `go/handlined` is built first.
 */
export function upgradeNow(o: { client: DaemonClient; exe: string; root: string; dist: string; sha?: string }): Promise<void> {
  const sha = o.sha ?? readBuildSha(o.dist);
  if (!sha) return Promise.reject(new Error('this build has no id, so the daemon cannot be matched to it'));
  const exe = path.resolve(o.exe);
  return upgradeDaemon({
    build: () =>
      new Promise<void>((resolve, reject) =>
        execFile('bash', ['tools/build-go.sh'], { cwd: o.root }, (err, _out, errOut) =>
          err ? reject(new Error(`build-go.sh: ${(errOut || err.message).trim().split('\n').pop()}`)) : resolve(),
        ),
      ),
    exe,
    sha,
    binarySha: () =>
      new Promise<string>((resolve, reject) =>
        execFile(exe, ['--version'], { timeout: 5000 }, (err, out) =>
          err ? reject(new Error(`${exe} does not start: ${err.message}`)) : resolve(out.trim()),
        ),
      ),
    upgrade: (a) => o.client.call('daemon.upgrade', a),
    version: async () => (await o.client.call('daemon.status', {})).version,
    wait: (ms) => new Promise((r) => setTimeout(r, ms)),
  });
}
