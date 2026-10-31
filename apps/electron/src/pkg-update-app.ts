// The installed app's update in Electron terms: the network, the files and the restart behind PkgPorts (pkg-update.ts).
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { app } from 'electron';
import { readBuildSha } from '#src/build-id.ts';
import { SelfUpdater, updateUrl, type PkgPorts } from '#src/pkg-update.ts';
import { leftovers, STAGING, swapBundle } from '#src/swap.ts';
import type { UpdateFlow } from '#src/update-flow.ts';

const run = (file: string, args: string[]): Promise<void> =>
  new Promise((resolve, reject) =>
    execFile(file, args, (err, _out, errOut) =>
      err ? reject(new Error(`${file}: ${(errOut || err.message).trim().split('\n').pop()}`)) : resolve(),
    ),
  );

/** The installed Handline.app: three folders above the executable (Contents/MacOS/Handline). */
const installedApp = () => path.resolve(app.getPath('exe'), '../../..');

/** The address the build reads its updates from, written into Resources by electron-builder when a publish address was set; null for any other build. */
export function packagedUpdateUrl(resources: string): string | null {
  try {
    return updateUrl(fs.readFileSync(path.join(resources, 'app-update.yml'), 'utf8'));
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
    download: async (url, progress) => {
      const r = await fetch(url);
      if (!r.ok || !r.body) throw new Error(`${url}: ${r.status}`);
      const total = Number(r.headers.get('content-length')) || 0;
      const file = path.join(app.getPath('temp'), `handline-update-${randomUUID()}.zip`);
      const out = fs.createWriteStream(file);
      let got = 0;
      for await (const chunk of r.body as unknown as AsyncIterable<Uint8Array>) {
        got += chunk.length;
        if (!out.write(chunk)) await new Promise((res) => out.once('drain', () => res(null)));
        if (total) progress(got / total);
      }
      await new Promise((res, rej) => out.end((e?: Error | null) => (e ? rej(e) : res(null))));
      return file;
    },
    sha512: (file) =>
      new Promise((resolve, reject) => {
        const h = createHash('sha512');
        fs.createReadStream(file)
          .on('data', (d) => h.update(d))
          .on('error', reject)
          .on('end', () => resolve(h.digest('base64')));
      }),
    stage: async (zip) => {
      const staging = path.join(path.dirname(installedApp()), STAGING);
      fs.rmSync(staging, { recursive: true, force: true });
      await run('ditto', ['-xk', zip, staging]);
      fs.rmSync(zip, { force: true });
      const staged = path.join(staging, path.basename(installedApp()));
      if (!fs.existsSync(path.join(staged, 'Contents', 'MacOS'))) throw new Error('the download has no app in it');
      return staged;
    },
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
  const base = packagedUpdateUrl(process.resourcesPath);
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
