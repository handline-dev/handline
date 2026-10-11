// What makes the app own its daemon, in Electron terms: the child processes, the tray, and the device token the app pairs for itself.
// The logic is in owner.ts, tray.ts and pair.ts, which a test drives; this file only connects them to Electron.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, nativeImage, Tray } from 'electron';
import { webSocketTransport } from '@handline/client';
import { pinnedWebSocketTransport, unixSocketTransport } from '@handline/client-node';
import { CHANNELS, fromWindow } from './bridge.ts';
import {
  Link,
  pairRemote,
  plainRemoteHost,
  helloOf,
  type Mode,
  type OfferInput,
  type Remote,
  type Settings,
  type SettingsFile,
} from './mode.ts';
import { commands, Owner, type Child, type Cmd } from './owner.ts';
import { ensureToken, founderKeyFile, phoneLink, TokenFile, type PairDeps } from './pair.ts';
import { phoneHtml } from './phone-html.ts';
import { grant } from './grant.ts';
import { onTailnet } from './tailnet.ts';
import { TRAY_PNG } from './tray-icon.ts';
import { trayMenu, type TrayFollow } from './tray.ts';

/** The menu-bar icon: a template image, so macOS tints it for a light or a dark bar. */
function trayIcon(): Electron.NativeImage {
  const img = nativeImage.createFromBuffer(TRAY_PNG.bytes(), { scaleFactor: TRAY_PNG.scale });
  img.setTemplateImage(true);
  return img;
}

/** A window with the QR of a fresh one-time link; rejects with the gateway's reason when it cannot make one (no network listener, no key). */
async function openPhone(pairing: PairDeps): Promise<void> {
  const l = await phoneLink(pairing);
  const minutes = Math.max(1, Math.round((Date.parse(l.expiresAt) - Date.now()) / 60_000));
  const w = new BrowserWindow({ width: 340, height: 440, resizable: false, title: 'Pair a phone', webPreferences: { sandbox: true } });
  w.setMenuBarVisibility(false);
  await w.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(phoneHtml(l.uri, minutes))}`);
}

/** The tray's way: the same window, the reason in a dialog. */
async function showPhone(pairing: PairDeps): Promise<void> {
  try {
    await openPhone(pairing);
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

/** Whether the gateway answers an HTTP request at all within three seconds. */
const reachable = (hostPort: string): Promise<boolean> =>
  fetch(`http://${hostPort}/`, { signal: AbortSignal.timeout(3000) }).then(
    () => true,
    () => false,
  );

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Its own process group: bin/ocp starts the daemon as a child of itself, and quit must stop that one too. */
function spawnChild(cmd: Cmd, log: number): Child {
  const p = spawn(cmd.file, cmd.args, { stdio: ['ignore', log, log], env: process.env, detached: true });
  const stop = () => {
    try {
      process.kill(-p.pid!, 'SIGTERM');
    } catch {
      p.kill();
    }
  };
  return { kill: stop, onExit: (f) => void p.on('exit', f).on('error', () => f(null)) };
}

/** Every failure of the daemon the app owns, with its reason, in daemon.log among the daemon's own lines: the tray forgets it. */
function logFailures(owner: Owner, log: number): void {
  owner.onChange(() => {
    if (owner.state === 'failed') fs.writeSync(log, `${new Date().toISOString()} app: the daemon is not up: ${owner.reason}\n`);
  });
}

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

function relaunchWith(file: SettingsFile, next: Settings): void {
  file.write(next);
  app.relaunch();
  app.quit();
}

export interface Own {
  /** Draws the tray menu again, after something it shows changed. */
  redraw(): void;
  /** Starts what is missing and pairs; resolves when the window can open (with or without a token), with the address the gateway ended up on. */
  ready(): Promise<string | undefined>;
  /** What the tray says in connect mode; the daemon's version is set once the shell's own connection says hello. */
  link: Link;
  /** Connect mode with a paired remote: where the window reaches it. */
  remote: Remote | null;
  /** The page's calls: switch the mode, or pair with another device; both relaunch the app. */
  switchMode(m: Mode): void;
  pair(i: OfferInput): Promise<void>;
  /** Opens the phone-pairing QR window; rejects with a sentence when this app has no gateway of its own to pair a phone with. */
  phone(): Promise<void>;
}

export function ownDaemon(o: {
  env: (k: string) => string | undefined;
  home: string;
  root: string;
  socket: string;
  gatewaySocket: string;
  /** host:port of the gateway, or undefined when the app is pointed at a daemon it does not own. */
  gateway: string | undefined;
  /** The MagicDNS name:port a phone's pairing link carries, when the gateway is on the Tailscale address. */
  advertise: string | null;
  origin: string;
  openWindow: () => void;
  settings: SettingsFile;
  /** The mode and pairing in force; connect mode never starts or stops a daemon. */
  current: Settings;
  /** Follow-main mode adds its items to the tray. */
  follow?: () => TrayFollow;
}): Own {
  const log = fs.openSync(path.join(app.getPath('userData'), 'daemon.log'), 'a');
  const owner = new Owner({
    socketUp: () => answers(o.socket),
    gatewayUp: () => answers(o.gatewaySocket),
    spawn: (cmd) => spawnChild(cmd, log),
    ...commands(o.env, o.root, o.gateway ?? '127.0.0.1:7443', o.advertise),
    wait: sleep,
  });
  app.on('quit', () => owner.stop());
  const tray = new Tray(trayIcon());
  tray.setToolTip('Handline');
  const link = new Link();
  const mode = o.current.mode;
  link.set('connecting', { address: o.gateway ?? null });
  const relaunchAs = (next: Settings) => relaunchWith(o.settings, next);
  const pair = async (i: OfferInput) => relaunchAs({ mode: 'connect', remote: await pairFrom(i) });
  const pairClipboard = () => pair({ link: clipboard.readText() }).catch((e: Error) => link.set('cannot-pair', { reason: e.message }));
  link.onChange(() => draw());
  const draw = () =>
    tray.setContextMenu(
      Menu.buildFromTemplate(
        trayMenu(
          owner.state,
          owner.reason,
          { open: o.openWindow, startDaemon: () => void owner.start(), quit: () => app.quit() },
          o.follow?.(),
          {
            mode,
            link,
            switchTo: () => relaunchAs({ ...o.current, mode: mode === 'connect' ? 'own' : 'connect' }),
            pair: () => void pairClipboard(),
            phone: o.current.remote ? undefined : () => void showPhone(pairing),
          },
        ),
      ),
    );
  owner.onChange(draw);
  logFailures(owner, log);
  draw();
  tray.on('click', o.openWindow);
  // The key stays in this process; the page gets the device token only, from its own origin's top frame.
  let token: string | null = null;
  let ownHost: string | null = null;
  ipcMain.on(CHANNELS.token, (e) => {
    e.returnValue = fromWindow(e, o.origin) ? grant(token, ownHost, owner) : null;
  });
  const pairing = localPairing(o.home, o.gatewaySocket);
  /** Connect mode: attach to what is there and never start it. */
  async function connect(): Promise<string | undefined> {
    const remote = o.current.remote;
    if (remote) {
      ownHost = plainRemoteHost(remote);
      token = remote.token;
      try {
        const version = await helloOf(remote, openRemote);
        link.set('connected', { address: remote.host });
        link.setVersion(version);
      } catch (e) {
        link.set('cannot-pair', { address: remote.host, reason: e instanceof Error ? e.message : String(e) });
      }
      return remote.host;
    }
    if (!o.gateway || !(await answers(o.socket)) || !(await answers(o.gatewaySocket))) {
      link.set('no-daemon', { address: null });
      return undefined;
    }
    ownHost = onTailnet(o.gateway) ? o.gateway.split(':')[0]! : null;
    try {
      token = await ensureToken(pairing, new TokenFile(path.join(app.getPath('userData'), 'device.json')), sleep, 3);
      link.set('connected', { address: o.gateway });
    } catch (e) {
      link.set('cannot-pair', { address: o.gateway, reason: e instanceof Error ? e.message : String(e) });
    }
    return o.gateway;
  }
  return {
    redraw: draw,
    link,
    remote: o.current.remote,
    switchMode: (m) => relaunchAs({ ...o.current, mode: m }),
    pair,
    phone: () =>
      o.current.remote ? Promise.reject(new Error('paired with another device: pair the phone on that device')) : openPhone(pairing),
    async ready() {
      if (mode === 'connect') return connect();
      if (!o.gateway) return undefined;
      await owner.start();
      ownHost = onTailnet(o.gateway) ? o.gateway.split(':')[0]! : null; // named even when nothing started: the page shows the reason (grant)
      if (owner.state !== 'running') return o.gateway;
      let address = o.gateway;
      // A Mac firewall can accept the connection on the Tailscale address and answer nothing. Loopback is only for a gateway this app
      // started and can replace: a gateway it did not start is not its to kill, and whatever else listens on loopback is not its own.
      if (onTailnet(address) && !(await reachable(address))) {
        const loopback = `127.0.0.1:${address.split(':')[1] ?? '7443'}`;
        const moved = await owner.moveGateway(
          commands(o.env, o.root, loopback).gateway,
          () => reachable(loopback),
          `the gateway does not answer on ${o.gateway} (a firewall?)`,
        );
        if (!moved) return o.gateway;
        address = loopback;
      }
      ownHost = onTailnet(address) ? address.split(':')[0]! : null;
      token = await ensureToken(pairing, new TokenFile(path.join(app.getPath('userData'), 'device.json')), sleep).catch(() => null);
      link.set('connected', { address });
      return address;
    },
  };
}
