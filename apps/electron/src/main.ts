import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { app, BrowserWindow, dialog, ipcMain, Notification, session, shell } from 'electron';
import { DaemonClient } from '@handline/client';
import { unixSocketTransport } from '@handline/client-node';
import { Desk } from './desk.ts';
import { Composer, LOGIN_PROBE, parseLoginEnv, sweepTemp, type EditorEnv } from './editor.ts';
import { Opener } from './open.ts';
import { ownDaemon } from './own.ts';
import { Page, CHANNELS } from './bridge.ts';
import { serveDir } from './serve.ts';
import { buildId, UNSENT_PROBE, Updater } from './update.ts';

const env = (k: string) => process.env[`HANDLINE_${k}`] ?? process.env[`OCP_${k}`];
/** The same rule the daemon uses: the environment's, else ~/.handline once it exists, else ~/.orcapipe. */
const home =
  env('HOME') ??
  (fs.existsSync(path.join(os.homedir(), '.handline')) ? path.join(os.homedir(), '.handline') : path.join(os.homedir(), '.orcapipe'));
/** The daemon's socket for this shell's own connection (badge, notices); the web app reaches the daemon by its WebSocket. */
const socket = env('SOCKET') ?? path.join(home, 'daemon.sock');
/** The Go gateway (host:port): the web app reaches it at /rpc with a device token the app got for itself. An explicit WS address with no gateway is the old plain mode. */
const gateway = env('GATEWAY') ?? (env('WS') ? undefined : '127.0.0.1:7443');
const gatewaySocket = path.join(home, 'gateway.sock');
/** The checkout the app runs from; a packaged app names its binaries with HANDLINE_OCP and HANDLINE_HANDLINED. */
const root = path.resolve(import.meta.dirname, '../../..');
/** The window always carries its daemon: a built page without `?daemon=` would take its own origin, which here is the static server. */
const ws = env('WS') ?? (gateway ? `ws://${gateway}/rpc` : 'ws://127.0.0.1:7777');
const dist = env('WEB_DIST') ?? path.resolve(import.meta.dirname, '../../web/dist');

let win: BrowserWindow | null = null;

/** VISUAL, EDITOR and PATH: the shell's own environment, else the login shell's, asked once (a Dock launch has neither). */
let login: Promise<EditorEnv> | null = null;
async function editorEnv(): Promise<EditorEnv> {
  if (process.env.VISUAL || process.env.EDITOR) return { VISUAL: process.env.VISUAL, EDITOR: process.env.EDITOR, PATH: process.env.PATH };
  login ??= new Promise<EditorEnv>((resolve) => {
    execFile(process.env.SHELL ?? '/bin/sh', ['-ilc', LOGIN_PROBE], { timeout: 3000, encoding: 'utf8' }, (err, out) =>
      resolve(err && !out ? {} : parseLoginEnv(out)),
    );
  });
  return login;
}

function show(): void {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

if (!app.requestSingleInstanceLock()) {
  app.quit(); // the first instance gets 'second-instance' and focuses its window
} else {
  app.on('second-instance', show);
  void app.whenReady().then(async () => {
    // The gateway takes a browser's socket only from its own origin; this page is served here, so the shell says so for that socket. The device token still decides.
    if (gateway)
      session.defaultSession.webRequest.onBeforeSendHeaders({ urls: [`ws://${gateway}/*`] }, (d, done) =>
        done({ requestHeaders: { ...d.requestHeaders, Origin: `http://${gateway}` } }),
      );
    // The device token is kept per page origin, so with a gateway the page keeps one port from launch to launch.
    const web = await serveDir(dist, Number(env('WEB_PORT') ?? (gateway ? 17443 : 0)));
    const origin = new URL(web.url).origin;
    const page = new Page(origin);
    const client = new DaemonClient({ client: 'electron', connect: () => unixSocketTransport(socket) });
    const own = ownDaemon({ env, home, root, socket, gatewaySocket, gateway, origin, openWindow: () => void openWindow() });
    ipcMain.handle(CHANNELS.screen, (e, arg) => page.screen(e, arg));
    ipcMain.handle(CHANNELS.unsent, (e, arg) => page.setUnsent(e, arg));
    // The worktree of an agent comes from the shell's own daemon connection, never from the page.
    const opener = new Opener(origin, {
      worktreeOf: async (agent) => (await client.call('agent.get', { agent })).worktree?.path ?? null,
      open: (p) => shell.openPath(p),
      reveal: (p) => shell.showItemInFolder(p),
    });
    const composer = new Composer(origin, { env: editorEnv, tmp: app.getPath('temp') });
    ipcMain.handle(CHANNELS.openPath, (e, arg) => opener.openPath(e, arg));
    ipcMain.handle(CHANNELS.compose, (e, arg) => composer.compose(e, arg));
    ipcMain.handle(CHANNELS.cancelCompose, (e, arg) => composer.cancel(e, arg));
    app.on('quit', () => composer.dispose());
    void sweepTemp(app.getPath('temp'));
    const openWindow = async (): Promise<void> => {
      if (win && !win.isDestroyed()) return show();
      win = new BrowserWindow({
        width: 1280,
        height: 860,
        title: 'Handline',
        show: false,
        // The page gets window.handline from the preload and nothing else (docs/electron-bridge.md §2).
        webPreferences: {
          contextIsolation: true,
          sandbox: true,
          nodeIntegration: false,
          preload: path.join(import.meta.dirname, 'preload.cjs'),
        },
      });
      win.once('ready-to-show', show);
      // The window shows the app and nothing else: a link goes to the browser (http/https only), and the window never leaves the loopback origin.
      win.webContents.setWindowOpenHandler(({ url }) => {
        if (/^https?:\/\//.test(url)) void shell.openExternal(url);
        return { action: 'deny' };
      });
      win.webContents.on('will-navigate', (e, url) => {
        if (new URL(url).origin !== origin) e.preventDefault();
      });
      await win.loadURL(`${web.url}?daemon=${encodeURIComponent(ws)}`);
    };
    // The app lives in the tray: closing the last window leaves it running.
    app.on('activate', () => void openWindow());
    app.on('window-all-closed', () => undefined);
    app.on('quit', () => web.close());

    const updater = new Updater({
      build: () => buildId({ page: path.join(dist, 'index.html'), main: import.meta.filename }),
      // The page says when it holds something unsent; until it has said so once, the shell looks into the page itself.
      idle: async () => {
        if (!win || win.isDestroyed() || composer.running) return false; // an open editor counts as unsent
        if (page.unsent !== null) return !page.unsent;
        return !(await win.webContents.executeJavaScript(UNSENT_PROBE).catch(() => true));
      },
      ask: async () =>
        (
          await dialog.showMessageBox(win!, {
            message: 'Handline was updated. Restart now?',
            buttons: ['Restart', 'Not now'],
            defaultId: 0,
            cancelId: 1,
          })
        ).response === 0,
      restart: () => {
        app.relaunch();
        app.quit();
      },
    });
    void own.ready().then(openWindow);
    const checking = setInterval(() => void updater.check().catch(() => undefined), 30_000);
    app.on('quit', () => clearInterval(checking));

    const desk = new Desk(client, {
      setBadge: (t) => {
        if (app.dock) app.dock.setBadge(t);
        else app.setBadgeCount(Number(t.replace('+', '')) || 0);
      },
      notify: (n, onClick) => {
        const notice = new Notification({ title: n.title, body: n.body });
        notice.on('click', onClick);
        notice.show();
      },
      screen: () => ({ focused: !!win?.isFocused(), agent: page.agent }),
      open: (n) => {
        show();
        if (n.target && win) win.webContents.send(CHANNELS.navigate, { screen: n.target.screen, id: n.target.id, event: n.event });
      },
    });
    // The client reconnects once it has been up; a daemon that is down at launch is retried here.
    void (async () => {
      for (;;) {
        try {
          await client.start();
          break;
        } catch {
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
      for (;;) {
        try {
          await desk.start();
          return;
        } catch {
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
    })();
  });
}
