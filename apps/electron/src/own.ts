// The app's daemon connection, in Electron terms: make sure the launchd service runs and attach to it (or to a paired remote), the tray,
// and the device token the app pairs for itself. The logic is in service.ts, tray.ts and pair.ts, which a test drives.
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, nativeImage, Tray } from 'electron';
import { webSocketTransport } from '@handline/client';
import { pinnedWebSocketTransport, unixSocketTransport } from '@handline/client-node';
import { CHANNELS, fromWindow } from './bridge.ts';
import { Link, pairRemote, plainRemoteHost, helloOf, type OfferInput, type Remote, type SettingsFile } from './mode.ts';
import { ensureService, ServiceStatus, type Ensured, stopService, type ServiceDeps } from './service.ts';
import { ensureToken, founderKeyFile, phoneLink, TokenFile, type PairDeps } from './pair.ts';
import { phoneHtml } from './phone-html.ts';
import { grant } from './grant.ts';
import { onTailnet } from './tailnet.ts';
import { singleWindow } from './single-window.ts';
import { TRAY_PNG } from './tray-icon.ts';
import { trayMenu, type TrayFollow } from './tray.ts';

/** The menu-bar icon: a template image, so macOS tints it for a light or a dark bar. */
function trayIcon(): Electron.NativeImage {
  const img = nativeImage.createFromBuffer(TRAY_PNG.bytes(), { scaleFactor: TRAY_PNG.scale });
  img.setTemplateImage(true);
  return img;
}

/** A window with the QR of a fresh one-time link; rejects with the gateway's reason when it cannot make one (no network listener, no key). */
const makePhone = async (pairing: PairDeps): Promise<{ win: BrowserWindow; expiresAt: number }> => {
  const l = await phoneLink(pairing);
  const minutes = Math.max(1, Math.round((Date.parse(l.expiresAt) - Date.now()) / 60_000));
  const w = new BrowserWindow({ width: 340, height: 440, resizable: false, title: 'Pair a phone', webPreferences: { sandbox: true } });
  w.setMenuBarVisibility(false);
  await w.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(phoneHtml(l.uri, minutes))}`);
  return { win: w, expiresAt: Date.parse(l.expiresAt) };
};

/** The tray's way: the same window, the reason in a dialog. */
async function showPhone(openPhone: () => Promise<void>): Promise<void> {
  try {
    await openPhone();
  } catch (e) {
    dialog.showErrorBox('Cannot pair a phone', e instanceof Error ? e.message : String(e));
  }
}
/** How the app proves itself to its own gateway's local socket. */
const localPairing = (home: string, gatewaySocket: string): PairDeps => ({
  readKey: () => readFounderKey(home),
  open: () => unixSocketTransport(gatewaySocket),
  name: 'Handline desktop',
});

/** Whether something is listening on this Unix socket. */
const answers = (file: string): Promise<boolean> =>
  new Promise((resolve) => {
    const c = net.createConnection(file);
    c.setTimeout(3000, () => (c.destroy(), resolve(false))); // a full backlog never answers; the probe must not stay open
    c.once('connect', () => (c.destroy(), resolve(true)));
    c.once('error', () => (c.destroy(), resolve(false)));
  });

/** The launchd plist `hl service install` writes. */
const plist = path.join(os.homedir(), 'Library', 'LaunchAgents', 'dev.orcapipe.daemon.plist');

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** The founder key from the protected store, or null while the daemon has not made it. */
function readFounderKey(home: string): string | null {
  try {
    return fs.readFileSync(founderKeyFile(os.homedir(), home), 'utf8').trim() || null;
  } catch {
    return null;
  }
}

const openRemote = (url: string, fp: string | null) => (fp ? pinnedWebSocketTransport(url, fp) : webSocketTransport(url));

const pairFrom = (i: OfferInput): Promise<Remote> => pairRemote(i, { open: openRemote, name: 'Handline desktop' });

const launchctl = (args: string[]): Promise<{ code: number; out: string }> =>
  new Promise((resolve) =>
    execFile('launchctl', args, { timeout: 15_000, encoding: 'utf8' }, (err, out, errOut) =>
      resolve({ code: err ? (typeof err.code === 'number' ? err.code : 1) : 0, out: `${out}${errOut}` }),
    ),
  );

/** The tray's Stop daemon asks first: every agent stops with the daemon. */
async function confirmStop(): Promise<boolean> {
  const r = await dialog.showMessageBox({
    type: 'warning',
    message: 'Stop the Handline daemon?',
    detail: 'Every agent stops with it, and nothing runs until the daemon is started again.',
    buttons: ['Stop daemon', 'Cancel'],
    defaultId: 1,
    cancelId: 1,
  });
  return r.response === 0;
}

export interface Own {
  /** Draws the tray menu again, after something it shows changed. */
  redraw(): void;
  /** Makes sure the daemon runs and pairs; resolves when the window can open (with or without a token), with the address the gateway is on. */
  ready(): Promise<string | undefined>;
  /** What the tray says: which daemon (address, version) and whether it answers. */
  link: Link;
  /** The paired remote the window reaches, or null for this Mac's daemon. */
  remote: Remote | null;
  /** The page's calls: pair with another device (relaunches), go back to this Mac's daemon (relaunches), show the phone QR. */
  pair(i: OfferInput): Promise<void>;
  useLocal(): void;
  /** Opens the phone-pairing QR window; rejects with a sentence when this app has no gateway of its own to pair a phone with. */
  phone(): Promise<void>;
}

export function connectDaemon(o: {
  home: string;
  socket: string;
  gatewaySocket: string;
  /** host:port of the gateway, or undefined when the app is pointed at a daemon it does not own. */
  gateway: string | undefined;
  origin: string;
  openWindow: () => void;
  settings: SettingsFile;
  /** The paired remote in force, or null. */
  remote: Remote | null;
  /** True for this Mac's live daemon service; false on a home of its own, where the app only attaches. */
  manage: boolean;
  /** Follow-main mode adds its items to the tray. */
  follow?: () => TrayFollow;
}): Own {
  const status = new ServiceStatus();
  const service: ServiceDeps = {
    launchctl,
    up: async () => (await answers(o.socket)) && (await answers(o.gatewaySocket)),
    installed: () => fs.existsSync(plist),
    domain: `gui/${os.userInfo().uid}`,
    plist,
    wait: sleep,
  };
  const tray = new Tray(trayIcon());
  tray.setToolTip('Handline');
  const link = new Link();
  link.set('connecting', { address: o.gateway ?? null });
  const relaunchWith = (remote: Remote | null) => {
    o.settings.write({ remote });
    app.relaunch();
    app.quit();
  };
  const pair = async (i: OfferInput) => relaunchWith(await pairFrom(i));
  const useLocal = () => relaunchWith(null);
  const pairClipboard = () => pair({ link: clipboard.readText() }).catch((e: Error) => link.set('cannot-pair', { reason: e.message }));
  /** Starts the same service again, and shows what it did. */
  const start = async () => {
    status.set('starting');
    const r: Ensured = o.manage
      ? await ensureService(service)
      : (await service.up())
        ? { ok: true, started: false }
        : { ok: false, reason: `no daemon answers on ${o.socket}` };
    status.set(r.ok ? 'running' : 'failed', r.ok ? '' : r.reason);
    return r;
  };
  const askStop = async () => {
    if (!(await confirmStop())) return;
    const stopped = await stopService(service);
    status.set(stopped.ok ? 'stopped' : 'failed', stopped.ok ? '' : stopped.reason);
    link.set('no-daemon');
  };
  link.onChange(() => draw());
  const draw = () =>
    tray.setContextMenu(
      Menu.buildFromTemplate(
        trayMenu(
          status.state,
          status.reason,
          { open: o.openWindow, startDaemon: () => void start(), stopDaemon: () => void askStop(), quit: () => app.quit() },
          o.follow?.(),
          {
            link,
            managed: o.manage,
            useLocal: o.remote ? useLocal : undefined,
            pair: () => void pairClipboard(),
            phone: o.remote ? undefined : () => void showPhone(openPhone),
          },
        ),
      ),
    );
  status.onChange(draw);
  draw();
  tray.on('click', o.openWindow);
  // The key stays in this process; the page gets the device token only, from its own origin's top frame.
  let token: string | null = null;
  let ownHost: string | null = null;
  ipcMain.on(CHANNELS.token, (e) => {
    e.returnValue = fromWindow(e, o.origin) ? grant(token, ownHost, status) : null;
  });
  const pairing = localPairing(o.home, o.gatewaySocket);
  const openPhone = singleWindow(() => makePhone(pairing));
  const tokenFile = () => new TokenFile(path.join(app.getPath('userData'), 'device.json'));
  return {
    redraw: draw,
    link,
    remote: o.remote,
    pair,
    useLocal,
    phone: () => (o.remote ? Promise.reject(new Error('paired with another device: pair the phone on that device')) : openPhone()),
    async ready() {
      const remote = o.remote;
      if (remote) {
        ownHost = plainRemoteHost(remote);
        token = remote.token;
        try {
          link.setVersion(await helloOf(remote, openRemote));
          link.set('connected', { address: remote.host });
        } catch (e) {
          link.set('cannot-pair', { address: remote.host, reason: e instanceof Error ? e.message : String(e) });
        }
        return remote.host;
      }
      if (!o.gateway) return undefined;
      ownHost = onTailnet(o.gateway) ? o.gateway.split(':')[0]! : null; // named even when nothing started: the page shows the reason (grant)
      const r = await start();
      if (!r.ok) {
        link.set('no-daemon', { address: o.gateway, reason: r.reason });
        return o.gateway;
      }
      try {
        token = await ensureToken(pairing, tokenFile(), sleep, 3);
        link.set('connected', { address: o.gateway });
      } catch (e) {
        link.set('cannot-pair', { address: o.gateway, reason: e instanceof Error ? e.message : String(e) });
      }
      return o.gateway;
    },
  };
}
