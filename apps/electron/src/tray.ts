// The tray's menu as plain data, so a test can read it; main.ts hands it to Electron's Menu.
import { badgeText } from '#src/notices.ts';
import type { FollowState } from '#src/follow.ts';
import type { Link, Mode } from '#src/mode.ts';
import type { DaemonState } from '#src/owner.ts';

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
  /** The Inbox, where the items waiting on the person are. */
  inbox(): void;
  settings(): void;
  /** Connect mode: try the daemon again. */
  reconnect(): void;
  /** Puts an address or a commit on the clipboard. */
  copy(text: string): void;
}

/** What the menu-bar icon shows: nothing extra, a red dot (items wait on the person), a blue dot (an update is ready) or the yellow icon (trouble). */
export type TrayIcon = 'plain' | 'turn' | 'update' | 'trouble';

/** What the tray shows besides the daemon: the My turn count, and whether the menu was opened with Option held (the details). */
export interface TrayView {
  unread: number;
  details: boolean;
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

/** The one line that says something is wrong, with its fix; null while all is well. */
function trouble(state: DaemonState, reason: string, a: TrayActions, m?: TrayMode): TrayItem[] | null {
  if (m?.mode === 'connect') {
    if (m.link.status === 'cannot-pair')
      return [
        { label: `⚠ Cannot reach the daemon: ${m.link.reason || 'the gateway did not answer'}`, enabled: false },
        { label: 'Reconnect', click: a.reconnect },
      ];
    if (m.link.status === 'no-daemon') return [{ label: '⚠ No daemon is running here', enabled: false }];
    return null;
  }
  if (state === 'stopped')
    return [
      { label: '⚠ The daemon stopped', enabled: false },
      { label: 'Start daemon', click: a.startDaemon },
    ];
  if (state === 'failed')
    return [
      { label: `⚠ The daemon failed${reason ? ` (${reason})` : ''}`, enabled: false },
      { label: 'Start daemon', click: a.startDaemon },
    ];
  return null;
}

/** What the icon carries: trouble first, then the person's turn, then an update. */
export function trayIcon(state: DaemonState, reason: string, a: TrayActions, view: TrayView, follow?: TrayFollow, m?: TrayMode): TrayIcon {
  if (trouble(state, reason, a, m)) return 'trouble';
  if (view.unread > 0) return 'turn';
  return follow?.state === 'available' ? 'update' : 'plain';
}

/** What Option shows and the usual menu leaves out: the mode, the daemon and its address, the commit, and the switches. Addresses and commits copy on a click. */
function detailItems(state: DaemonState, reason: string, a: TrayActions, follow?: TrayFollow, m?: TrayMode): TrayItem[] {
  const items: TrayItem[] = [];
  if (m) items.push({ label: MODE_WORDS[m.mode], enabled: false });
  if (m?.mode === 'connect') items.push(...connectItems(m.link));
  else items.push({ label: reason && state === 'failed' ? `${STATUS_WORDS[state]} (${reason})` : STATUS_WORDS[state], enabled: false });
  if (m) items.push({ label: daemonWords(m.link), ...(m.link.address ? { click: () => a.copy(m.link.address!) } : { enabled: false }) });
  if (follow) items.push({ label: `Handline is on main ${follow.sha.slice(0, 7)}`, click: () => a.copy(follow.sha) });
  if (follow?.state !== 'available' && follow) items.push({ label: 'Check for update', click: follow.check });
  if (m?.mode === 'connect') items.push({ label: 'Pair with the link on the clipboard', click: m.pair });
  if (m) items.push({ label: m.mode === 'connect' ? 'Switch to Own daemon' : 'Switch to Connect', click: m.switchTo });
  return items;
}

/** The menu: Open, Pair a phone, Settings, Quit; one more line only when something needs the person (items waiting, an update ready, trouble). */
export function trayMenu(
  state: DaemonState,
  reason: string,
  a: TrayActions,
  follow?: TrayFollow,
  m?: TrayMode,
  view: TrayView = { unread: 0, details: false },
): TrayItem[] {
  const items: TrayItem[] = [{ label: 'Open Handline', click: a.open }];
  const notes: TrayItem[] = [];
  if (view.unread > 0) notes.push({ label: `Needs you · ${badgeText(view.unread)}`, click: a.inbox });
  notes.push(...(trouble(state, reason, a, m) ?? []));
  if (follow?.state === 'available' && follow.next) notes.push({ label: 'Restart to update', click: follow.restart });
  if (follow?.state === 'updating') notes.push({ label: 'Updating…', enabled: false });
  if (follow?.state === 'failed')
    notes.push({ label: `Update failed: ${follow.reason}`, enabled: false }, { label: 'Check again', click: follow.check });
  if (notes.length) items.push({ type: 'separator' }, ...notes);
  items.push({ type: 'separator' });
  if (m?.phone) items.push({ label: 'Pair a phone…', click: m.phone });
  items.push({ label: 'Settings…', click: a.settings });
  if (view.details) items.push({ type: 'separator' }, ...detailItems(state, reason, a, follow, m));
  items.push({ type: 'separator' }, { label: 'Quit Handline', click: a.quit });
  return items;
}
