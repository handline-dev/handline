// The installed app's update in Electron terms: the network, the files and the restart behind PkgPorts (pkg-update.ts).
import fs from 'node:fs';
import path from 'node:path';
import { app } from 'electron';
import { downloadTo, sha512Of, unpackApp } from '#src/pkg-files.ts';
import { readBuildSha } from '#src/build-id.ts';
import { SelfUpdater, type PkgPorts } from '#src/pkg-update.ts';
import { leftovers, STAGING, swapBundle } from '#src/swap.ts';
import type { UpdateFlow } from '#src/update-flow.ts';

/** The installed Handline.app: three folders above the executable (Contents/MacOS/Handline). */
const installedApp = () => path.resolve(app.getPath('exe'), '../../..');

/** The address the build reads its updates from, written into its package.json by electron-builder (electron-builder.config.mjs); null for any other build. */
export function packagedUpdateUrl(appPath: string): string | null {
  try {
    const url = (JSON.parse(fs.readFileSync(path.join(appPath, 'package.json'), 'utf8')) as { handlineUpdateUrl?: unknown })
      .handlineUpdateUrl;
    return typeof url === 'string' && url ? url : null;
  } catch {
    return null;
  }
}

/** Removes what an earlier swap left beside the app. */
export function sweepSwap(): void {
  const parent = path.dirname(installedApp());
  try {
    for (const p of leftovers(parent, fs.readdirSync(parent), path.basename(installedApp())))
      fs.rmSync(p, { recursive: true, force: true });
  } catch {
    /* the folder cannot be listed: nothing to remove */
  }
}

function ports(o: { idle: () => Promise<boolean>; upgrade: (sha?: string) => Promise<void> }): PkgPorts {
  return {
    current: () => app.getVersion(),
    text: async (url) => {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`${url}: ${r.status}`);
      return r.text();
    },
    download: (url, progress) => downloadTo(app.getPath('temp'), url, progress),
    sha512: sha512Of,
    stage: (zip) => unpackApp(zip, path.join(path.dirname(installedApp()), STAGING), path.basename(installedApp())),
    idle: o.idle,
    swapAndRestart: async (staged) => {
      const installed = installedApp();
      const sha = readBuildSha(path.join(staged, 'Contents', 'Resources', 'app.asar', 'dist')) ?? undefined;
      const aside = swapBundle(installed, staged);
      try {
        await o.upgrade(sha); // the daemon is moved onto the new app's own handlined, which now stands at the installed path
      } catch (e) {
        fs.renameSync(installed, staged);
        fs.renameSync(aside, installed);
        throw e;
      }
      app.relaunch();
      app.quit();
    },
  };
}

/** Starts looking for a newer build of the installed app, once a minute after launch and then every half hour. Returns what stops it. */
export function startPkgUpdate(o: {
  flow: UpdateFlow;
  idle: () => Promise<boolean>;
  upgrade: (sha?: string) => Promise<void>;
}): () => void {
  const base = packagedUpdateUrl(app.getAppPath());
  if (!base) return () => undefined;
  sweepSwap();
  const updater = new SelfUpdater(ports(o), o.flow, base);
  const look = () => void updater.check();
  const first = setTimeout(look, 60_000);
  const every = setInterval(look, 30 * 60_000);
  return () => {
    clearTimeout(first);
    clearInterval(every);
  };
}
