// What makes the app own its daemon, in Electron terms: the child processes, the tray, and the device token the app pairs for itself.
// The logic is in owner.ts, tray.ts and pair.ts, which a test drives; this file only connects them to Electron.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { app, clipboard, ipcMain, Menu, nativeImage, Tray } from 'electron';
import { webSocketTransport } from '@handline/client';
import { pinnedWebSocketTransport, unixSocketTransport } from '@handline/client-node';
import { CHANNELS, fromWindow } from './bridge.ts';
import { Link, pairRemote, plainRemoteHost, remoteUrl, type Mode, type Remote, type Settings, type SettingsFile } from './mode.ts';
import { commands, Owner } from './owner.ts';
import { ensureToken, founderKeyFile, TokenFile } from './pair.ts';
import { onTailnet } from './tailnet.ts';
import { trayMenu, type TrayFollow } from './tray.ts';

/** Whether something is listening on this Unix socket. */
const answers = (file: string): Promise<boolean> =>
  new Promise((resolve) => {
    const c = net.createConnection(file);
    c.once('connect', () => (c.destroy(), resolve(true)));
    c.once('error', () => resolve(false));
  });

/** Whether the gateway answers an HTTP request at all within three seconds. */
const reachable = (hostPort: string): Promise<boolean> =>
  fetch(`http://${hostPort}/`, { signal: AbortSignal.timeout(3000) }).then(
    () => true,
    () => false,
  );

/** Whether something accepts a TCP connection at host:port within three seconds. */
const tcpUp = (hostPort: string): Promise<boolean> =>
  new Promise((resolve) => {
    const [host, port] = hostPort.split(/:(?=\d+$)/);
    const c = net.createConnection({ host: host!, port: Number(port), timeout: 3000 });
    c.once('connect', () => (c.destroy(), resolve(true)));
    c.once('timeout', () => (c.destroy(), resolve(false)));
    c.once('error', () => resolve(false));
  });

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** A 16×16 ring for the tray. */
const TRAY_ICON =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><circle cx="8" cy="8" r="6" fill="none" stroke="black" stroke-width="2.5"/></svg>',
  );

export interface Own {
  /** Draws the tray menu again, after something it shows changed. */
  redraw(): void;
  /** Starts what is missing and pairs; resolves when the window can open (with or without a token), with the address the gateway ended up on. */
  ready(): Promise<string | undefined>;
  /** What the tray says in connect mode; the daemon's version is set once the shell's own connection says hello. */
  link: Link;
  /** Connect mode with a paired remote: where the window reaches it. */
  remote: Remote | null;
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
    spawn: (cmd) => {
      // Its own process group: bin/ocp starts the daemon as a child of itself, and quit must stop that one too.
      const p = spawn(cmd.file, cmd.args, { stdio: ['ignore', log, log], env: process.env, detached: true });
      const stop = () => {
        try {
          process.kill(-p.pid!, 'SIGTERM');
        } catch {
          p.kill();
        }
      };
      return { kill: stop, onExit: (f) => void p.on('exit', f).on('error', () => f(null)) };
    },
    ...commands(o.env, o.root, o.gateway ?? '127.0.0.1:7443', o.advertise),
    wait: sleep,
  });
  app.on('quit', () => owner.stop());
  const tray = new Tray(nativeImage.createFromDataURL(TRAY_ICON));
  tray.setToolTip('Handline');
  const link = new Link();
  link.set('connecting', { address: o.gateway ?? null });
  const mode = o.current.mode;
  const relaunchAs = (next: Settings) => {
    o.settings.write(next);
    app.relaunch();
    app.quit();
  };
  const pairClipboard = async () => {
    try {
      const remote = await pairRemote({ link: clipboard.readText() }, { open: openRemote, name: 'Handline desktop' });
      relaunchAs({ mode: 'connect', remote });
    } catch (e) {
      link.set('cannot-pair', { reason: e instanceof Error ? e.message : String(e) });
    }
  };
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
          },
        ),
      ),
    );
  owner.onChange(draw);
  draw();
  tray.on('click', o.openWindow);
  // The key stays in this process; the page gets the device token only, from its own origin's top frame.
  let token: string | null = null;
  let ownHost: string | null = null;
  ipcMain.on(CHANNELS.token, (e) => {
    e.returnValue = fromWindow(e, o.origin) ? { token, ownHost } : null;
  });
  const openRemote = (url: string, fingerprint: string | null) =>
    fingerprint ? pinnedWebSocketTransport(url, fingerprint) : webSocketTransport(url);
  const pairing = {
    readKey: () => {
      try {
        return fs.readFileSync(founderKeyFile(os.homedir(), o.home), 'utf8').trim() || null;
      } catch {
        return null;
      }
    },
    open: () => unixSocketTransport(o.gatewaySocket),
    name: 'Handline desktop',
  };
  /** Connect mode: attach to what is there and never start it. */
  async function connect(): Promise<string | undefined> {
    const remote = o.current.remote;
    if (remote) {
      ownHost = plainRemoteHost(remote);
      token = remote.token;
      link.set((await tcpUp(remote.host)) ? 'connected' : 'cannot-pair', { address: remote.host, reason: `nothing answers at ${remote.host}` });
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
    async ready() {
      if (mode === 'connect') return connect();
      if (!o.gateway) return undefined;
      await owner.start();
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
