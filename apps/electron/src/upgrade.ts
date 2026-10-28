import { shaOf } from '@handline/app-core';

// One Update replaces the app and the daemon as one set: the daemon is built, told to re-exec onto the app's own binary, and waited for.

export interface UpgradeDeps {
  /** Builds the daemon from the checkout; absent for a packaged app, which carries its own. */
  build?(): Promise<void>;
  /** The app's own handlined (absolute) and the 12-char sha of the build the app is. */
  exe: string;
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
  try {
    await d.upgrade({ exe: d.exe, sha: d.sha });
  } catch (e) {
    throw new Error(`the daemon did not upgrade: ${e instanceof Error ? e.message : String(e)}`, { cause: e });
  }
  for (let i = 0; i < (d.polls ?? 120); i++) {
    await d.wait(500);
    const v = await d.version().catch(() => '');
    if (v && shaOf(v).slice(0, 12) === d.sha.slice(0, 12)) return;
  }
  throw new Error('the daemon did not come back on the new build within 60 seconds');
}
