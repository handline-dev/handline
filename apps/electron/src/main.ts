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
import { followMain, takeRoute } from './follow-app.ts';
import { parseMode, remoteUrl, SettingsFile, type Mode, type Remote } from './mode.ts';
import { ownDaemon, type Own } from './own.ts';
import { gatewayAddress, magicDnsName, onTailnet, TAILSCALE_CLIS } from './tailnet.ts';
import { Connection, Page, CHANNELS } from './bridge.ts';
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
let gateway = env('WS') && !env('GATEWAY') ? undefined : gatewayAddress(env('GATEWAY'), os.networkInterfaces());
const gatewaySocket = path.join(home, 'gateway.sock');
/** The checkout the app runs from; a packaged app names its binaries with HANDLINE_OCP and HANDLINE_HANDLINED. */
const root = path.resolve(import.meta.dirname, '../../..');
/** The window always carries its daemon: a built page without `?daemon=` would take its own origin, which here is the static server. */
let ws = env('WS') ?? (gateway ? `ws://${gateway}/rpc` : 'ws://127.0.0.1:7777');
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
  if (!win || win.isDestroyed()) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

/** HANDLINE_DEV_URL: a vite dev server stands in for the built dist, so the page hot-reloads as the sources change (a dev aid). */
async function pageServer(): Promise<{ url: string; close: () => void }> {
  const devUrl = env('DEV_URL');
  return devUrl ? { url: devUrl, close: () => undefined } : serveDir(dist, Number(env('WEB_PORT') ?? (gateway ? 17443 : 17444)));
}

/** `name.ts.net:port` for the gateway when Tailscale is up and names this machine, else null (the pairing link then carries the address). */
async function advertised(gateway: string): Promise<string | null> {
  for (const cli of TAILSCALE_CLIS) {
    const out = await new Promise<string>((resolve) =>
      execFile(cli, ['status', '--json'], { timeout: 5000, encoding: 'utf8' }, (err, o) => resolve(err ? '' : o)),
    );
    const name = magicDnsName(out);
    if (name) return `${name}:${gateway.split(':')[1] ?? '7443'}`;
  }
  return null;
}

/** A paired remote gateway has a self-signed certificate: the page trusts it by the fingerprint pairing handed over, and no other. */
function trustFingerprint(remote: Remote): void {
  app.on('certificate-error', (e, _wc, url, _err, cert, trust) => {
    e.preventDefault();
    const got = Buffer.from(cert.fingerprint.replace(/^sha256\//, ''), 'base64').toString('hex');
    trust(!!remote.fingerprint && new URL(url).host === remote.host && got === remote.fingerprint);
  });
}

function handleConnection(origin: string, own: Own, mode: Mode, remote: Remote | null): void {
  const c = new Connection(origin, {
    mode: () => ({ mode, remote: remote?.host ?? null }),
    setMode: (m) => own.switchMode(m),
    pair: (i) => own.pair(i),
    phone: () => own.phone(),
  });
  ipcMain.handle(CHANNELS.mode, (e) => c.mode(e));
  ipcMain.handle(CHANNELS.setMode, (e, arg) => c.setMode(e, arg));
  ipcMain.handle(CHANNELS.phone, (e) => c.phone(e));
  ipcMain.handle(CHANNELS.pairRemote, (e, arg) => c.pairRemote(e, arg));
}

/** Runs `f` until it stops throwing, two seconds apart. */
async function untilUp(f: () => Promise<unknown>): Promise<void> {
  for (;;) {
    try {
      return void (await f());
    } catch {
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
}

/** The app window: the page gets window.handline and nothing else, and the window never leaves the loopback origin. */
function createWindow(origin: string, onReady: () => void): BrowserWindow {
  const w = new BrowserWindow({
    width: 1280,
    height: 860,
    title: 'Handline',
    show: false,
    // No OS title bar; the traffic lights sit over the sidebar (styles/base.css makes room).
    ...(os.platform() === 'darwin' ? { titleBarStyle: 'hiddenInset' as const, trafficLightPosition: { x: 14, y: 13 } } : {}),
    // The page gets window.handline from the preload and nothing else (docs/electron-bridge.md §2).
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      preload: path.join(import.meta.dirname, 'preload.cjs'),
    },
  });
  w.once('ready-to-show', onReady);
  // The window shows the app and nothing else: a link goes to the browser (http/https only), and the window never leaves the loopback origin.
  w.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  w.webContents.on('will-navigate', (e, url) => {
    if (new URL(url).origin !== origin) e.preventDefault();
  });
  return w;
}

// A port for DevTools protocol checks of a run (HANDLINE_DEBUG_PORT=9333).
if (env('DEBUG_PORT')) app.commandLine.appendSwitch('remote-debugging-port', env('DEBUG_PORT')!);

// HANDLINE_DATA: a data folder of one's own - a dev instance then keeps its settings, its device
// token and its single-instance lock apart from the everyday app and runs next to it (a dev aid).
const data = env('DATA');
if (data) app.setPath('userData', data);

if (!app.requestSingleInstanceLock()) {
  app.quit(); // the first instance gets 'second-instance' and focuses its window
} else {
  app.on('second-instance', show);
  void app.whenReady().then(async () => {
    // The gateway takes a browser's socket only from its own origin; this page is served here, so the shell says so for that socket. The device token still decides.
    session.defaultSession.webRequest.onBeforeSendHeaders({ urls: ['ws://*/*', 'wss://*/*'] }, (d, done) =>
      done({
        requestHeaders:
          gateway && new URL(d.url).host === gateway ? { ...d.requestHeaders, Origin: `http://${gateway}` } : d.requestHeaders,
      }),
    );
    const settings = new SettingsFile(path.join(app.getPath('userData'), 'settings.json'));
    const current = settings.read(parseMode(env('MODE')));
    const remote = current.mode === 'connect' ? current.remote : null;
    if (!remote && gateway && !env('GATEWAY') && !onTailnet(gateway))
      console.warn(`Tailscale is down: the gateway listens on ${gateway} only, so a phone cannot pair`);
    if (remote) {
      gateway = remote.host;
      ws = remoteUrl(remote);
      trustFingerprint(remote);
    }
    // The device token, the layouts and the settings are kept per page origin, so the page keeps one port from launch to launch.
    const web = await pageServer();
    const origin = new URL(web.url).origin;
    const page = new Page(origin);
    const client = new DaemonClient({ client: 'electron', connect: () => unixSocketTransport(socket) });
    // Follow-main mode (pnpm app:main): the app runs from its own checkout of main and offers to restart when main moves.
    const following = env('FOLLOW') ? followMain({ dir: root, window: () => win, changed: () => own.redraw() }) : null;
    const f = following?.follower;
    const own: Own = ownDaemon({
      env,
      home,
      root,
      socket,
      gatewaySocket,
      gateway,
      advertise: gateway && onTailnet(gateway) ? await advertised(gateway) : null,
      origin,
      settings,
      current,
      openWindow: () => void openWindow(),
      follow:
        f &&
        (() => ({
          sha: f.sha,
          state: f.state,
          next: f.next?.sha ?? null,
          reason: f.reason,
          check: () => void f.look(),
          restart: () => void f.restartNow(),
        })),
    });
    handleConnection(origin, own, current.mode, remote);
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
      win = createWindow(origin, show);
      await win.loadURL(`${web.url}?daemon=${encodeURIComponent(ws)}${takeRoute()}`);
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
    void own
      .ready()
      .then((at) => {
        if (!remote && at && at !== gateway && !env('WS')) ws = `ws://${(gateway = at)}/rpc`;
      })
      .then(openWindow)
      .then(() => following?.start());
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
      await untilUp(() => client.start());
      if (!remote) own.link.setVersion(client.hello?.daemon);
      await untilUp(() => desk.start());
    })();
  });
}
