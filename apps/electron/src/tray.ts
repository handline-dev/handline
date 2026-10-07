// The tray's menu as plain data, so a test can read it; main.ts hands it to Electron's Menu.
import type { FollowState } from './follow.ts';
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

export function trayMenu(state: DaemonState, reason: string, a: TrayActions, follow?: TrayFollow): TrayItem[] {
  const status: TrayItem = {
    label: reason && state === 'failed' ? `${STATUS_WORDS[state]} (${reason})` : STATUS_WORDS[state],
    enabled: false,
  };
  const items: TrayItem[] = [{ label: 'Open Handline', click: a.open }, { type: 'separator' }, status];
  if (state === 'failed' || state === 'stopped') items.push({ label: 'Start daemon', click: a.startDaemon });
  if (follow) items.push({ type: 'separator' }, ...followItems(follow));
  items.push({ type: 'separator' }, { label: 'Quit Handline', click: a.quit });
  return items;
}
