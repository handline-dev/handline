// The Electron side of upgrading the daemon: its binary, its build step, and the calls on the shell's own connection.
import { execFile } from 'node:child_process';
import path from 'node:path';
import type { DaemonClient } from '@handline/client';
import { readBuildSha } from '#src/build-id.ts';
import { upgradeDaemon } from '#src/upgrade.ts';

/**
 * Moves the running daemon onto this app's own handlined and waits for it. `sha` is the build to move to (the app's, read now: an update
 * has just built a newer one than the app runs); a checkout builds `go/handlined` first, a packaged app carries its own.
 */
export function upgradeNow(o: {
  client: DaemonClient;
  /** The handlined this app runs with: the packaged one, or the checkout's go/handlined. */
  exe: string;
  /** Whether the checkout's handlined is built first (false for a packaged app). */
  build: boolean;
  root: string;
  /** The folder with build.json. */
  dist: string;
  sha?: string;
}): Promise<void> {
  const sha = o.sha ?? readBuildSha(o.dist);
  if (!sha) return Promise.reject(new Error('this build has no id, so the daemon cannot be matched to it'));
  const exe = path.resolve(o.exe);
  return upgradeDaemon({
    build: o.build
      ? () =>
          new Promise<void>((resolve, reject) =>
            execFile('go', ['build', '-C', 'go', '-o', 'handlined', './cmd/handlined'], { cwd: o.root }, (err, _out, errOut) =>
              err ? reject(new Error(`go build: ${(errOut || err.message).trim().split('\n').pop()}`)) : resolve(),
            ),
          )
      : undefined,
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
