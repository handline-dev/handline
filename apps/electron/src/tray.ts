// The tray's menu as plain data, so a test can read it; main.ts hands it to Electron's Menu.
import type { FollowState } from './follow.ts';
import type { Link } from './mode.ts';
import type { ServiceState } from './service.ts';

export interface TrayItem {
  label?: string;
  type?: 'separator';
  enabled?: boolean;
  click?: () => void;
}

export interface TrayActions {
  open(): void;
  /** Starts the daemon service again (the same launchd service the app attaches to). */
  startDaemon(): void;
  /** Asks first, then stops the daemon service: the one way the app ever stops it. */
  stopDaemon(): void;
  /** Quits the app and nothing else: the daemon and its agents keep running. */
  quit(): void;
}

export const STATUS_WORDS: Record<ServiceState, string> = {
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

/** Which daemon the app talks to, and the pairing items. */
export interface TrayLink {
  link: Pick<Link, 'status' | 'address' | 'version' | 'reason'>;
  /** False for an instance on its own home (a team's dev instance): the app only attaches there and never starts or stops a service. */
  managed?: boolean;
  /** Paired with another device's gateway: the item that goes back to this Mac's daemon. */
  useLocal?(): void;
  /** Pair with the `handline://` link on the clipboard. */
  pair(): void;
  /** Show a QR a phone scans to pair. Absent when the app is on another device's gateway. */
  phone?(): void;
}

const daemonWords = (l: TrayLink['link']): string =>
  l.address ? `Daemon: ${l.address}${l.version ? ` · ${l.version}` : ''}` : 'Daemon: none';

const LINK_WORDS: Record<string, string> = {
  connected: 'Connected',
  connecting: 'Connecting…',
  'no-daemon': 'The daemon is not running',
};

export function trayMenu(state: ServiceState, reason: string, a: TrayActions, follow?: TrayFollow, m?: TrayLink): TrayItem[] {
  const remote = !!m?.useLocal;
  const managed = m?.managed !== false;
  const items: TrayItem[] = [{ label: 'Open Handline', click: a.open }, { type: 'separator' }];
  if (m) items.push({ label: daemonWords(m.link), enabled: false });
  if (remote && m) {
    items.push({ label: LINK_WORDS[m.link.status] ?? `Cannot pair: ${m.link.reason || 'the gateway did not answer'}`, enabled: false });
  } else {
    items.push({ label: reason && state === 'failed' ? `${STATUS_WORDS[state]} (${reason})` : STATUS_WORDS[state], enabled: false });
    if (managed && (state === 'failed' || state === 'stopped')) items.push({ label: 'Start daemon', click: a.startDaemon });
  }
  if (m?.useLocal) items.push({ label: "Use this Mac's daemon", click: m.useLocal });
  if (m) items.push({ label: 'Pair with the link on the clipboard', click: m.pair });
  if (m?.phone) items.push({ label: 'Pair a phone…', click: m.phone });
  if (!remote && managed && state === 'running') items.push({ type: 'separator' }, { label: 'Stop daemon…', click: a.stopDaemon });
  if (follow) items.push({ type: 'separator' }, ...followItems(follow));
  items.push({ type: 'separator' }, { label: 'Quit Handline', click: a.quit });
  return items;
}
