import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { app, BrowserWindow, desktopCapturer, dialog, ipcMain, Notification, session, shell, type WebContents } from 'electron';
import { DaemonClient } from '@handline/client';
import { unixSocketTransport } from '@handline/client-node';
import { drawsTray, inBackground } from '#src/background.ts';
import { isDebugWindow } from '#src/debug-window.ts';
import { Desk } from '#src/desk.ts';
import { Composer, LOGIN_PROBE, parseLoginEnv, sweepTemp, type EditorEnv } from '#src/editor.ts';
import { serveCapture } from '#src/capture.ts';
import { gitView, scratch } from '#src/git-view.ts';
import { Dropper } from '#src/drop.ts';
import { Opener } from '#src/open.ts';
import { OpenIn } from '#src/open-in.ts';
import { Devices } from '#src/devices.ts';
import { Preview } from '#src/preview.ts';
import { followMain, takeRoute } from '#src/follow-app.ts';
import type { Follower } from '#src/follow.ts';
import { parseMode, remoteUrl, SettingsFile, type Link, type Mode, type Remote } from '#src/mode.ts';
import { ownDaemon, readFounderKey, type Own } from '#src/own.ts';
import { gatewayAddress, magicDnsName, onTailnet, TAILSCALE_CLIS } from '#src/tailnet.ts';
import { Cli, Connection, Page, Settings, CHANNELS } from '#src/bridge.ts';
import { serveDir } from '#src/serve.ts';
import { buildId, UNSENT_PROBE, Updater } from '#src/update.ts';
import { bundleAt } from '#src/bundle.ts';
import { readBuildSha } from '#src/build-id.ts';
import { upgradeNow } from '#src/daemon-upgrade.ts';
import { cliState, linkCli } from '#src/cli.ts';

const env = (k: string) => process.env[`HANDLINE_${k}`] ?? process.env[`OCP_${k}`];
/** The same rule the daemon uses: the environment's, else ~/.handline once it exists, else ~/.orcapipe. */
const home =
  env('HOME') ??
  (fs.existsSync(path.join(os.homedir(), '.handline')) ? path.join(os.homedir(), '.handline') : path.join(os.homedir(), '.orcapipe'));
/** The daemon's socket for this shell's own connection (badge, notices); the web app reaches the daemon by its WebSocket. */
const socket = env('SOCKET') ?? path.join(home, 'daemon.sock');
// The founder key goes only to a daemon socket inside the daemon's home: HANDLINE_SOCKET can name another process's socket.
const founderKey = () => {
  const rel = path.relative(home, socket);
  return rel === '..' || rel.startsWith(`..${path.sep}`) ? null : readFounderKey(home);
};
/** An installed app runs the daemon it carries and serves the web build it carries, unless the environment names others. */
const carried = bundleAt(app.isPackaged, process.resourcesPath);
if (carried) process.env.HANDLINE_HANDLINED ??= carried.handlined;
/** The Go gateway (host:port): the web app reaches it at /rpc with a device token the app got for itself. An explicit WS address with no gateway is the old plain mode. */
let gateway = env('WS') && !env('GATEWAY') ? undefined : gatewayAddress(env('GATEWAY'), os.networkInterfaces());
const gatewaySocket = path.join(home, 'gateway.sock');
/** The checkout the app runs from; a packaged app names its binaries with HANDLINE_OCP and HANDLINE_HANDLINED. */
const root = path.resolve(import.meta.dirname, '../../..');
/** The window always carries its daemon: a built page without `?daemon=` would take its own origin, which here is the static server. */
let ws = env('WS') ?? (gateway ? `ws://${gateway}/rpc` : 'ws://127.0.0.1:7777');
const APP_ICON = path.join(import.meta.dirname, '../assets/icon.png');
const dist = env('WEB_DIST') ?? carried?.web ?? path.resolve(import.meta.dirname, '../../web/dist');

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

/** The gateway takes a browser's socket only from its own origin; this page is served here, so the shell says so for that socket. The device token still decides. */
function allowGatewayOrigin(): void {
  session.defaultSession.webRequest.onBeforeSendHeaders({ urls: ['ws://*/*', 'wss://*/*'] }, (d, done) =>
    done({
      requestHeaders: gateway && new URL(d.url).host === gateway ? { ...d.requestHeaders, Origin: `http://${gateway}` } : d.requestHeaders,
    }),
  );
}

const background = inBackground(process.env);
/** The build this app is, read once at launch: an update builds a newer one on disk while this one still runs. */
const runningBuild = readBuildSha(import.meta.dirname);

/** Brings the window to the front, except in a background run, whose window stays hidden (the DevTools port still draws and captures it). */
function show(): void {
  if (!win || win.isDestroyed() || background) return;
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

/** The window connects as a paired device, which may not read or write settings; the shell's founder connection does it for the page. */
function handleSettings(origin: string, client: DaemonClient, remote: Remote | null): void {
  const otherDevice = new Error('This app is paired with another device; change its settings there.');
  const s = new Settings(origin, {
    get: (agent) => (remote ? Promise.reject(otherDevice) : client.call('settings.get', { agent })),
    set: (changes, agent) => (remote ? Promise.reject(otherDevice) : client.call('settings.set', { changes, agent })),
  });
  ipcMain.handle(CHANNELS.settingsGet, (e, arg) => s.get(e, arg));
  ipcMain.handle(CHANNELS.settingsSet, (e, arg) => s.set(e, arg));
}

function handleConnection(origin: string, own: Own, mode: Mode, remote: Remote | null, network: boolean, f?: Follower): void {
  const c = new Connection(origin, {
    build: () => ({ sha: runningBuild, canRestart: !remote }),
    restartDaemon: () => own.restartDaemon(),
    mode: () => ({ mode, remote: remote?.host ?? null }),
    follow: () => (f ? { sha: f.sha, state: f.state, next: f.next?.sha ?? null } : null),
    check: async () => void (await f?.look()),
    setMode: (m) => own.switchMode(m),
    pair: (i) => own.pair(i),
    phone: () => own.phone(),
    network: () => network,
    setNetwork: (on) => own.setNetwork(on),
  });
  ipcMain.handle(CHANNELS.mode, (e) => c.mode(e));
  ipcMain.handle(CHANNELS.setMode, (e, arg) => c.setMode(e, arg));
  ipcMain.handle(CHANNELS.follow, (e) => c.follow(e));
  ipcMain.handle(CHANNELS.checkUpdate, (e) => c.checkUpdate(e));
  ipcMain.handle(CHANNELS.phone, (e) => c.phone(e));
  ipcMain.handle(CHANNELS.network, (e) => c.network(e));
  ipcMain.handle(CHANNELS.setNetwork, (e, arg) => c.setNetwork(e, arg));
  ipcMain.handle(CHANNELS.pairRemote, (e, arg) => c.pairRemote(e, arg));
  serveCapture(ipcMain, origin, { sources: (thumbnailSize) => desktopCapturer.getSources({ types: ['window'], thumbnailSize }) });
}

function handleDevices(origin: string): void {
  const devices = new Devices(origin);
  ipcMain.handle(CHANNELS.deviceShot, (e, arg) => devices.shot(e, arg));
  ipcMain.handle(CHANNELS.deviceTap, (e, arg) => devices.tap(e, arg));
  ipcMain.handle(CHANNELS.deviceList, (e) => devices.list(e));
  ipcMain.handle(CHANNELS.deviceSwipe, (e, arg) => devices.swipe(e, arg));
  ipcMain.handle(CHANNELS.deviceText, (e, arg) => devices.text(e, arg));
}

/** The shell's own connection to the daemon. */
const shellClient = (): DaemonClient =>
  new DaemonClient({ client: 'electron', connect: () => unixSocketTransport(socket), key: founderKey });

/** On every connect, a daemon restarted by an update included, the tray learns which build it now is. */
function watchVersion(client: DaemonClient, link: Link): void {
  link.setVersion(client.hello?.daemon);
  client.onState((st) => st === 'open' && link.setVersion(client.hello?.daemon));
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

/** Moves the daemon onto this app's build: the checkout runs go/handlined, built here; an installed app carries its own. A paired remote daemon is not this app's to move. */
function upgrader(client: DaemonClient, remote: Remote | null): (sha?: string) => Promise<void> {
  return (sha) =>
    remote
      ? Promise.resolve()
      : upgradeNow({
          client,
          exe: env('HANDLINED') ?? carried?.handlined ?? `${root}/go/handlined`,
          build: !carried,
          root,
          dist: import.meta.dirname,
          sha,
        });
}

function makeUpdater(page: Page, composer: Composer, upgrade: () => Promise<void>): Updater {
  return new Updater({
    build: () => buildId({ page: path.join(dist, 'index.html'), main: import.meta.filename }),
    // The page says when it holds something unsent; until it has said so once, the shell looks into the page itself.
    idle: async () => {
      if (!win || win.isDestroyed() || composer.running) return false; // an open editor counts as unsent
      if (page.unsent !== null) return !page.unsent;
      return !(await win.webContents.executeJavaScript(UNSENT_PROBE).catch(() => true));
    },
    ask: async () =>
      !background &&
      (
        await dialog.showMessageBox(win!, {
          message: 'Handline was updated. Restart now?',
          buttons: ['Restart', 'Not now'],
          defaultId: 0,
          cancelId: 1,
        })
      ).response === 0,
    // The daemon moves onto the new build first; if it will not, the app stays and says why.
    restart: async () => {
      await upgrade();
      app.relaunch();
      app.quit();
    },
    failed: (reason) => void dialog.showMessageBox(win!, { type: 'error', message: 'Handline was not restarted', detail: reason }),
  });
}

/** The calls the shell makes on its own founder connection for the page: settings, and openIn. */
function handleFounderCalls(origin: string, client: DaemonClient, remote: Remote | null, tmp: string): void {
  handleSettings(origin, client, remote);
  handleOpenIn(origin, client, tmp);
}

/** openIn and openInApps with the machine's own pieces: the shell's founder connection for the folder and the tmux command, and /usr/bin/open through execFile. */
function handleOpenIn(origin: string, client: DaemonClient, tmp: string): void {
  const openIn = new OpenIn(origin, {
    platform: process.platform,
    home: os.homedir(),
    tmp,
    worktreeOf: async (agent) => (await client.call('agent.get', { agent })).worktree?.path ?? null,
    attachOf: async (agent) => (await client.call('terminal.open', { agent })).attach?.argv ?? null,
    exec: (file, args) => new Promise((resolve, reject) => execFile(file, args, (err) => (err ? reject(err) : resolve()))),
    exists: (p) =>
      fs.promises.access(p).then(
        () => true,
        () => false,
      ),
    later: (fn, ms) => void setTimeout(fn, ms).unref(),
  });
  ipcMain.handle(CHANNELS.openIn, (e, arg) => openIn.open(e, arg));
  ipcMain.handle(CHANNELS.openInApps, (e) => openIn.apps(e));
}

/** The app window: the page gets window.handline and nothing else, and the window never leaves the loopback origin. */
function createWindow(origin: string, onReady: () => void): BrowserWindow {
  const w = new BrowserWindow({
    width: 1280,
    height: 860,
    title: 'Handline',
    icon: APP_ICON,
    show: false,
    // No OS title bar; the traffic lights sit over the sidebar (styles/base.css makes room).
    ...(os.platform() === 'darwin' ? { titleBarStyle: 'hiddenInset' as const, trafficLightPosition: { x: 14, y: 15 } } : {}),
    // The page gets window.handline from the preload and nothing else (docs/electron-bridge.md §2).
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      preload: path.join(import.meta.dirname, 'preload.cjs'),
      // A hidden window would be throttled; a background run's page must keep drawing for its checks and captures.
      backgroundThrottling: !background,
    },
  });
  // The traffic lights are gone in full screen: the page drops the room it keeps for them (shell.ts).
  const tellFull = () => w.webContents.send(CHANNELS.fullScreen, w.isFullScreen());
  w.on('enter-full-screen', tellFull);
  w.on('leave-full-screen', tellFull);
  w.webContents.on('did-finish-load', tellFull);
  w.once('ready-to-show', onReady);
  confine(w.webContents, origin);
  return w;
}

/**
 * The window shows the app and nothing else: a link goes to the browser (http/https only), and the window never leaves the loopback
 * origin. The one window the page may open is the debug window (Settings' bottom button), on the same origin and with the same
 * preferences, and it is confined the same way.
 */
function confine(contents: WebContents, origin: string): void {
  contents.setWindowOpenHandler(({ url }) => {
    if (isDebugWindow(url, origin))
      return { action: 'allow', overrideBrowserWindowOptions: { width: 1280, height: 820, title: 'Terminals (debug)' } };
    if (/^https?:\/\//.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  contents.on('did-create-window', (child) => confine(child.webContents, origin));
  contents.on('will-navigate', (e, url) => {
    if (new URL(url).origin !== origin) e.preventDefault();
  });
}

// A port for DevTools protocol checks of a run (HANDLINE_DEBUG_PORT=9333).
if (env('DEBUG_PORT')) app.commandLine.appendSwitch('remote-debugging-port', env('DEBUG_PORT')!);

// HANDLINE_DATA: a data folder of one's own - a dev instance then keeps its settings, its device
// token and its single-instance lock apart from the everyday app and runs next to it (a dev aid).
const data = env('DATA');
if (data) app.setPath('userData', data);

if (!app.requestSingleInstanceLock({ background })) {
  app.quit(); // the first instance gets 'second-instance' and focuses its window, unless this run is a background one
} else {
  app.on('second-instance', (_e, _argv, _cwd, data) => (data as { background?: boolean } | undefined)?.background || show());
  if (background) app.setActivationPolicy('accessory'); // no dock icon, and the app never becomes the active one
  void app.whenReady().then(async () => {
    // An unpackaged app shows Electron's icon in the dock until it is told otherwise.
    app.dock?.setIcon(APP_ICON);
    allowGatewayOrigin();
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
    // The shell's own calls (an agent's worktree, the badge) are the founder's: hello carries the key from the protected store, as pairing does.
    const client = shellClient();
    // Follow-main mode (pnpm app): the app runs from its own checkout of main and offers to restart when main moves.
    const upgradeTo = upgrader(client, remote);
    const following = env('FOLLOW')
      ? followMain({ dir: root, window: () => win, changed: () => own.redraw(), upgradeDaemon: upgradeTo })
      : null;
    const f = following?.follower;
    const own: Own = ownDaemon({
      env,
      loginPath: async () => (await editorEnv()).PATH,
      home,
      root,
      tray: drawsTray(process.env, app.isPackaged),
      socket,
      gatewaySocket,
      gateway,
      advertise: gateway && onTailnet(gateway) ? await advertised(gateway) : null,
      web: current.network ? dist : null,
      origin,
      settings,
      current,
      build: runningBuild,
      upgradeDaemon: () => upgradeTo(),
      openWindow: () => void openWindow(),
      openPage: (screen) => void openWindow().then(() => win?.webContents.send(CHANNELS.navigate, { screen, id: '', event: 'tray' })),
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
    handleConnection(origin, own, current.mode, remote, current.network === true, f);
    const cliDir = path.join(os.homedir(), '.local', 'bin');
    const cli = new Cli(origin, { state: () => cliState(carried?.hl ?? null, cliDir), link: () => linkCli(carried!.hl, cliDir) });
    ipcMain.handle(CHANNELS.cli, (e) => cli.state(e));
    ipcMain.handle(CHANNELS.linkCli, (e) => cli.link(e));
    ipcMain.handle(CHANNELS.screen, (e, arg) => page.screen(e, arg));
    ipcMain.handle(CHANNELS.unsent, (e, arg) => page.setUnsent(e, arg));
    const preview = new Preview(origin, () => win);
    ipcMain.handle(CHANNELS.preview, (e, arg) => preview.set(e, arg));
    ipcMain.handle(CHANNELS.capturePreview, (e) => preview.capture(e));
    handleDevices(origin);
    handleFounderCalls(origin, client, remote, app.getPath('temp'));
    // The worktree of an agent comes from the shell's own daemon connection, never from the page.
    const opener = new Opener(origin, {
      worktreeOf: async (agent) => (await client.call('agent.get', { agent })).worktree?.path ?? null,
      open: (p) => shell.openPath(p),
      reveal: (p) => shell.showItemInFolder(p),
      git: gitView,
      openExternal: (url) => shell.openExternal(url),
      scratch: (name, bytes) => scratch(app.getPath('temp'), name, bytes),
    });
    const composer = new Composer(origin, { env: editorEnv, tmp: app.getPath('temp') });
    ipcMain.handle(CHANNELS.openPath, (e, arg) => opener.openPath(e, arg));
    const dropper = new Dropper(origin, async (agent) => (await client.call('agent.get', { agent })).worktree?.path ?? null);
    ipcMain.handle(CHANNELS.bringIn, (e, arg) => dropper.bringIn(e, arg));
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

    const updater = makeUpdater(page, composer, () => upgradeTo());
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
      setBadge: (t, n) => {
        own.setUnread(n);
        if (app.dock) app.dock.setBadge(t);
        else app.setBadgeCount(n);
      },
      notify: (n, onClick) => {
        if (background) return;
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
      if (!remote) watchVersion(client, own.link);
      await untilUp(() => desk.start());
    })();
  });
}
