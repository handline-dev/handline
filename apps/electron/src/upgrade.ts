import { samePrefix, shaOf } from '@handline/app-core';

// One Update replaces the app and the daemon as one set: the daemon is built, told to re-exec onto the app's own binary, and waited for.

export interface UpgradeDeps {
  /** Builds the daemon from the checkout; absent for a packaged app, which carries its own. */
  build?(): Promise<void>;
  exe: string;
  /** What `exe --version` prints, which is the sha the daemon will hold it to; it is read after the build. */
  binarySha(): Promise<string>;
  sha: string;
  /** daemon.upgrade over the shell's own connection; rejects with the daemon's own sentence when it refuses. */
  upgrade(a: { exe: string; sha: string }): Promise<unknown>;
  /** The version the daemon reports now (status.version); rejects while it is restarting. */
  version(): Promise<string>;
  wait(ms: number): Promise<void>;
  /** How long the daemon gets to come back, in polls of 500 ms. */
  polls?: number;
}

/** Resolves when the daemon runs the app's build; rejects with the reason, and the app must not relaunch then. */
export async function upgradeDaemon(d: UpgradeDeps): Promise<void> {
  await d.build?.();
  const sha = shaOf(await d.binarySha());
  if (!samePrefix(sha, d.sha)) throw new Error(`${d.exe} is build ${sha}, not the app's ${d.sha.slice(0, 9)}`);
  try {
    await d.upgrade({ exe: d.exe, sha });
  } catch (e) {
    throw new Error(`the daemon did not upgrade: ${e instanceof Error ? e.message : String(e)}`, { cause: e });
  }
  for (let i = 0; i < (d.polls ?? 120); i++) {
    await d.wait(500);
    const v = await d.version().catch(() => '');
    if (v && samePrefix(shaOf(v), sha)) return;
  }
  throw new Error('the daemon did not come back on the new build within 60 seconds');
}
