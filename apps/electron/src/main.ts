import os from 'node:os';
import path from 'node:path';
import { app, BrowserWindow, Notification } from 'electron';
import { DaemonClient } from '@handline/client';
import { unixSocketTransport } from '@handline/client-node';
import { Desk } from './desk.ts';
import { hashOfAgent } from './notices.ts';
import { serveDir } from './serve.ts';

const env = (k: string) => process.env[`HANDLINE_${k}`] ?? process.env[`OCP_${k}`];
const home = env('HOME') ?? path.join(os.homedir(), '.handline');
/** The daemon's socket for this shell's own connection (badge, notices); the web app reaches the daemon by its WebSocket. */
const socket = env('SOCKET') ?? path.join(home, 'daemon.sock');
const ws = env('WS');
const dist = env('WEB_DIST') ?? path.resolve(import.meta.dirname, '../../web/dist');

let win: BrowserWindow | null = null;

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
    const web = await serveDir(dist);
    win = new BrowserWindow({ width: 1280, height: 860, title: 'Handline', show: false });
    win.once('ready-to-show', show);
    await win.loadURL(ws ? `${web.url}?daemon=${encodeURIComponent(ws)}` : web.url);
    app.on('activate', show);
    app.on('window-all-closed', () => app.quit());
    app.on('quit', () => web.close());

    const client = new DaemonClient({ client: 'electron', connect: () => unixSocketTransport(socket) });
    const desk = new Desk(client, {
      setBadge: (t) => { if (app.dock) app.dock.setBadge(t); else app.setBadgeCount(Number(t.replace('+', '')) || 0); },
      notify: (n, onClick) => {
        const notice = new Notification({ title: n.title, body: n.body });
        notice.on('click', onClick);
        notice.show();
      },
      screen: () => ({ focused: !!win?.isFocused(), hash: new URL(win?.webContents.getURL() || web.url).hash }),
      open: (agent) => {
        show();
        if (agent && win) void win.webContents.executeJavaScript(`location.hash = ${JSON.stringify(hashOfAgent(agent))}`);
      },
    });
    // The client reconnects once it has been up; a daemon that is down at launch is retried here.
    void (async () => {
      for (;;) {
        try { await client.start(); break; }
        catch { await new Promise((r) => setTimeout(r, 2000)); }
      }
      await desk.start().catch(() => undefined);
    })();
  });
}
