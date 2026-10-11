// The tray's menu as plain data, so a test can read it; main.ts hands it to Electron's Menu.
import type { FollowState } from './follow.ts';
import type { Link, Mode } from './mode.ts';
import type { DaemonState } from './owner.ts';

export interface TrayItem {
  label?: string;
  type?: 'separator';
  enabled?: boolean;
  click?: () => void;
}

export interface TrayActions {
  open(): void;
  startDaemon(): void;
  quit(): void;
}

export const STATUS_WORDS: Record<DaemonState, string> = {
  starting: 'Daemon: starting…',
  running: 'Daemon: running',
  stopped: 'Daemon: stopped',
  failed: 'Daemon: failed',
};

/** Follow-main mode: the commit the app runs, whether main has moved, and what the tray offers. */
export interface TrayFollow {
  sha: string;
  state: FollowState;
  /** The newer main's short sha, while there is one. */
  next: string | null;
  reason: string;
  check(): void;
  restart(): void;
}

export function followItems(f: TrayFollow): TrayItem[] {
  const on = `Handline is on main ${f.sha.slice(0, 7)}`;
  if (f.state === 'updating') return [{ label: 'Updating…', enabled: false }];
  if (f.state === 'failed')
    return [
      { label: `Update failed: ${f.reason}`, enabled: false },
      { label: 'Check again', click: f.check },
    ];
  if (f.state === 'available' && f.next)
    return [
      { label: `${on}; main is now ${f.next.slice(0, 7)}`, enabled: false },
      { label: 'Restart to update', click: f.restart },
    ];
  return [
    { label: on, enabled: false },
    { label: 'Check for update', click: f.check },
  ];
}

/** Which mode is on and which daemon the app talks to; `switchTo` flips the mode. */
export interface TrayMode {
  mode: Mode;
  /** Connect mode: the link's state. Own mode: the address and version of the daemon it started. */
  link: Pick<Link, 'status' | 'address' | 'version' | 'reason'>;
  switchTo(): void;
  /** Connect mode: pair with the `handline://` link on the clipboard. */
  pair(): void;
  /** Own mode, or Connect to this computer's daemon: show a QR a phone scans to pair. Absent when the app is on another device's gateway. */
  phone?(): void;
}

const MODE_WORDS: Record<Mode, string> = { connect: 'Mode: Connect to a daemon', own: 'Mode: Own daemon' };

const daemonWords = (l: TrayMode['link']): string =>
  l.address ? `Daemon: ${l.address}${l.version ? ` · ${l.version}` : ''}` : 'Daemon: none';

/** Connect mode's lines: it never starts a daemon, so there is no Start item, only what answers. */
function connectItems(l: TrayMode['link']): TrayItem[] {
  const status =
    l.status === 'connected'
      ? 'Connected'
      : l.status === 'connecting'
        ? 'Connecting…'
        : l.status === 'no-daemon'
          ? 'No daemon is running here: connect to another device'
          : `Cannot pair: ${l.reason || 'the gateway did not answer'}`;
  return [
    { label: daemonWords(l), enabled: false },
    { label: status, enabled: false },
  ];
}

export function trayMenu(state: DaemonState, reason: string, a: TrayActions, follow?: TrayFollow, m?: TrayMode): TrayItem[] {
  const status: TrayItem = {
    label: reason && state === 'failed' ? `${STATUS_WORDS[state]} (${reason})` : STATUS_WORDS[state],
    enabled: false,
  };
  const items: TrayItem[] = [{ label: 'Open Handline', click: a.open }, { type: 'separator' }];
  if (m) items.push({ label: MODE_WORDS[m.mode], enabled: false });
  if (m?.mode === 'connect') items.push(...connectItems(m.link));
  else {
    if (m) items.push({ label: daemonWords(m.link), enabled: false });
    items.push(status);
    if (state === 'failed' || state === 'stopped') items.push({ label: 'Start daemon', click: a.startDaemon });
  }
  if (m?.mode === 'connect') items.push({ label: 'Pair with the link on the clipboard', click: m.pair });
  if (m?.phone) items.push({ label: 'Pair a phone…', click: m.phone });
  if (m) items.push({ label: m.mode === 'connect' ? 'Switch to Own daemon' : 'Switch to Connect', click: m.switchTo });
  if (follow) items.push({ type: 'separator' }, ...followItems(follow));
  items.push({ type: 'separator' }, { label: 'Quit Handline', click: a.quit });
  return items;
}
