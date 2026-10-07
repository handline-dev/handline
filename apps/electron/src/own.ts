// What makes the app own its daemon, in Electron terms: the child processes, the tray, and the device token the app pairs for itself.
// The logic is in owner.ts, tray.ts and pair.ts, which a test drives; this file only connects them to Electron.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { app, ipcMain, Menu, nativeImage, Tray } from 'electron';
import { unixSocketTransport } from '@handline/client-node';
import { CHANNELS, fromWindow } from './bridge.ts';
import { commands, Owner } from './owner.ts';
import { ensureToken, founderKeyFile, TokenFile } from './pair.ts';
import { trayMenu } from './tray.ts';

/** Whether something is listening on this Unix socket. */
const answers = (file: string): Promise<boolean> =>
  new Promise((resolve) => {
    const c = net.createConnection(file);
    c.once('connect', () => (c.destroy(), resolve(true)));
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
  /** Starts what is missing and pairs; resolves when the window can open (with or without a token). */
  ready(): Promise<void>;
}

export function ownDaemon(o: {
  env: (k: string) => string | undefined;
  home: string;
  root: string;
  socket: string;
  gatewaySocket: string;
  /** host:port of the gateway, or undefined when the app is pointed at a daemon it does not own. */
  gateway: string | undefined;
  origin: string;
  openWindow: () => void;
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
    ...commands(o.env, o.root, o.gateway ?? '127.0.0.1:7443'),
    wait: sleep,
  });
  app.on('quit', () => owner.stop());
  const tray = new Tray(nativeImage.createFromDataURL(TRAY_ICON));
  tray.setToolTip('Handline');
  const draw = () =>
    tray.setContextMenu(
      Menu.buildFromTemplate(
        trayMenu(owner.state, owner.reason, { open: o.openWindow, startDaemon: () => void owner.start(), quit: () => app.quit() }),
      ),
    );
  owner.onChange(draw);
  draw();
  tray.on('click', o.openWindow);
  // The key stays in this process; the page gets the device token only, from its own origin's top frame.
  let token: string | null = null;
  ipcMain.on(CHANNELS.token, (e) => {
    e.returnValue = fromWindow(e, o.origin) ? token : null;
  });
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
  return {
    async ready() {
      if (!o.gateway) return;
      await owner.start();
      if (owner.state !== 'running') return;
      token = await ensureToken(pairing, new TokenFile(path.join(app.getPath('userData'), 'device.json')), sleep).catch(() => null);
    },
  };
}
