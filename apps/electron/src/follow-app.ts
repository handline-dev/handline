// Follow-main mode in Electron terms: the checkout the app runs from and a watch on GitHub's main through the repo it was cloned from. When main moves the
// app prepares the update by itself and reports to the one update notice (update-flow.ts). The decision and the update are in follow.ts.
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { app, type BrowserWindow } from 'electron';
import { Follower, peekMain, watchMain, WEB_NEXT } from '#src/follow.ts';
import type { UpdateFlow } from '#src/update-flow.ts';

const sh = (file: string, args: string[], cwd: string): Promise<string> =>
  new Promise((resolve, reject) =>
    execFile(file, args, { cwd, maxBuffer: 16 << 20 }, (err, out, errOut) =>
      err ? reject(new Error(`${file} ${args[0] ?? ''}: ${(errOut || err.message).trim().split('\n').pop()}`)) : resolve(String(out)),
    ),
  );

/** Where the page's route is kept across the relaunch, so the same agent is on screen after it. */
export const routeFile = () => path.join(app.getPath('userData'), 'route.txt');
export const takeRoute = (): string => {
  try {
    return fs.readFileSync(routeFile(), 'utf8');
  } catch {
    return '';
  } finally {
    fs.rmSync(routeFile(), { force: true });
  }
};

export function followMain(o: {
  dir: string;
  window: () => BrowserWindow | null;
  changed: () => void;
  upgradeDaemon: (sha: string) => Promise<void>;
  flow: UpdateFlow;
}): {
  follower: Follower;
  start(): Promise<void>;
} {
  let source = '';
  const web = path.join(o.dir, 'apps', 'web');
  const follower = new Follower({
    run: (file, args) => sh(file, args, o.dir),
    peek: () => peekMain((...a) => sh('git', ['-C', source, ...a], o.dir).then((s) => s.trim())),
    upgradeDaemon: o.upgradeDaemon,
    swapWeb: () => {
      if (!fs.existsSync(path.join(web, WEB_NEXT))) return;
      fs.rmSync(path.join(web, 'dist'), { recursive: true, force: true });
      fs.renameSync(path.join(web, WEB_NEXT), path.join(web, 'dist'));
    },
    beforeRelaunch: () => {
      const url = o.window()?.webContents.getURL() ?? '';
      try {
        fs.writeFileSync(routeFile(), new URL(url).hash);
      } catch {
        /* no page: it opens on its own default route */
      }
    },
    relaunch: () => {
      app.relaunch();
      app.quit();
    },
  });
  follower.onChange((fresh) => {
    o.changed();
    const flow = o.flow;
    switch (follower.state) {
      case 'available':
        if (fresh) void follower.prepare(); // the person is asked once it is built, not before
        break;
      case 'updating':
        flow.preparing(follower.step, follower.fraction);
        break;
      case 'ready':
        flow.ready(() => void follower.restartNow());
        break;
      case 'failed':
        flow.failed(follower.reason, () => void follower.retry(), follower.log);
        break;
      case 'current':
        flow.idle();
    }
  });
  return {
    follower,
    async start() {
      source = (await sh('git', ['remote', 'get-url', 'origin'], o.dir)).trim();
      const common = (await sh('git', ['-C', source, 'rev-parse', '--git-common-dir'], o.dir)).trim();
      watchMain(
        path.resolve(source, common),
        () => void follower.look(),
        (e) => follower.watchFailed(e),
      );
      // A fetch anywhere in the source repo moves the watched ref; a window coming to the front looks by itself too, at most once a minute.
      // A put-off update is raised again once a day.
      let looked = 0;
      app.on('browser-window-focus', () => {
        o.flow.remind();
        if (Date.now() - looked < 60_000) return;
        looked = Date.now();
        void follower.look();
      });
      await follower.init();
    },
  };
}
