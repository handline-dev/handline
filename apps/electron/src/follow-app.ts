// Follow-main mode in Electron terms: the checkout the app runs from, a watch on GitHub's main through the repo it was cloned from, and the popover in the window.
// The decision and the update are in follow.ts; this connects them to git, the window and the tray.
import { execFile } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { app, ipcMain, WebContentsView, type BrowserWindow } from 'electron';
import { Follower, peekMain, watchMain, type Main } from '#src/follow.ts';
import { popoverHtml } from '#src/popover-html.ts';

const sh = (file: string, args: string[], cwd: string): Promise<string> =>
  new Promise((resolve, reject) =>
    execFile(file, args, { cwd, maxBuffer: 16 << 20 }, (err, out, errOut) =>
      err ? reject(new Error(`${file} ${args[0] ?? ''}: ${(errOut || err.message).trim().split('\n').pop()}`)) : resolve(String(out)),
    ),
  );

const SIZE = { w: 360, h: 112, margin: 16 };

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
}): {
  follower: Follower;
  start(): Promise<void>;
} {
  let view: WebContentsView | null = null;
  let unplace = () => {};
  const hide = () => {
    const w = o.window();
    if (view && w && !w.isDestroyed()) w.contentView.removeChildView(view);
    unplace();
    view?.webContents.close();
    view = null;
  };
  const place = () => {
    const w = o.window();
    if (!view || !w || w.isDestroyed()) return;
    const [cw, ch] = w.getContentSize();
    view.setBounds({ x: cw! - SIZE.w - SIZE.margin, y: ch! - SIZE.h - SIZE.margin, width: SIZE.w, height: SIZE.h });
  };
  const show = (main: Main) => {
    const w = o.window();
    if (!w || w.isDestroyed()) return;
    hide();
    view = new WebContentsView({
      webPreferences: { sandbox: true, contextIsolation: true, preload: path.join(import.meta.dirname, 'popover-preload.cjs') },
    });
    w.contentView.addChildView(view);
    place();
    w.on('resize', place);
    unplace = () => w.off('resize', place);
    void view.webContents.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(popoverHtml(main, randomBytes(16).toString('hex'))));
  };

  let source = '';
  const follower = new Follower({
    run: (file, args) => sh(file, args, o.dir),
    peek: () => peekMain((...a) => sh('git', ['-C', source, ...a], o.dir).then((s) => s.trim())),
    upgradeDaemon: o.upgradeDaemon,
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
    if (follower.state === 'available' && fresh && follower.next) show(follower.next);
    else if (follower.state !== 'available') hide();
  });
  // The buttons come from the popover's own page only.
  ipcMain.on('handline:follow-restart', (e) => {
    if (e.sender === view?.webContents) void follower.restartNow();
  });
  ipcMain.on('handline:follow-later', (e) => {
    if (e.sender !== view?.webContents) return;
    hide();
    follower.later();
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
      let looked = 0;
      app.on('browser-window-focus', () => {
        if (Date.now() - looked < 60_000) return;
        looked = Date.now();
        void follower.look();
      });
      await follower.init();
    },
  };
}
