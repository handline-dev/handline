// The tray's menu as plain data, so a test can read it; main.ts hands it to Electron's Menu.
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

export function trayMenu(state: DaemonState, reason: string, a: TrayActions): TrayItem[] {
  const status: TrayItem = {
    label: reason && state === 'failed' ? `${STATUS_WORDS[state]} (${reason})` : STATUS_WORDS[state],
    enabled: false,
  };
  const items: TrayItem[] = [{ label: 'Open Handline', click: a.open }, { type: 'separator' }, status];
  if (state === 'failed' || state === 'stopped') items.push({ label: 'Start daemon', click: a.startDaemon });
  items.push({ type: 'separator' }, { label: 'Quit Handline', click: a.quit });
  return items;
}
