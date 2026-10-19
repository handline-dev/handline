// The surface between the web app and the desktop shell (docs/electron-bridge.md): types and the one table from an event's target to a route.
// Pure, no DOM: the shell's preload and the web app both import it.

import type { Methods, SettingsChanges } from '@handline/client';

/** The settings the Settings page edits, the only keys the shell sets on its behalf (the other daemon settings are not the page's to change). */
export const PAGE_SETTINGS = [
  'active_cap',
  'park_timeout_s',
  'handoff_note',
  'auto_accept_dialogs',
  'notice_digest_s',
  'bg_notice_s',
  'bg_grace_s',
  'bg_check_s',
] as const;
export type PageSettings = Pick<SettingsChanges, (typeof PAGE_SETTINGS)[number]>;

/** What every call returns when it does not succeed: calls never throw across the window's edge. */
export type Fail<C extends string> = { ok: false; code: C; message: string };

/** A clicked notice: the notifications event's type and the `target` the daemon put on it. */
export interface NavigateTo {
  screen: string;
  id: string;
  event: string;
}

export interface ShellApi {
  /** Present when the shell is there; a change that breaks a call makes it 2. */
  api: 1;
  /** The agent whose workspace is on screen, or null. */
  screen(s: { agent: string | null }): Promise<{ ok: true } | Fail<'bad_request'>>;
  /** True while a restart would lose something. */
  unsent(on: boolean): Promise<{ ok: true } | Fail<'bad_request'>>;
  /** Open a path a message holds in the default app, or reveal it when it is something that runs. Absent in a shell that lacks it. */
  openPath?(a: {
    path: string;
    agent?: string;
    /** Branch names the message holds: a repo path opens the version on one of them (read-only), else the worktree's file, else main, else GitHub. */
    branches?: string[];
  }): Promise<{ ok: true; how: 'opened' | 'revealed' | 'branch' | 'main' | 'github' } | Fail<'bad_request' | 'not_found' | 'unavailable'>>;
  /** Edit text in the person's editor and read it back. Absent in a shell that lacks it. */
  compose?(a: {
    text?: string;
    ext?: 'md' | 'txt';
  }): Promise<
    | { ok: true; text: string; changed: boolean }
    | Fail<
        | 'bad_request'
        | 'no_editor'
        | 'terminal_editor'
        | 'bad_editor'
        | 'not_waiting'
        | 'editor_failed'
        | 'cancelled'
        | 'busy'
        | 'too_large'
        | 'unavailable'
      >
  >;
  cancelCompose?(): Promise<{ ok: true } | Fail<'bad_request'>>;
  /** The windows open on this machine, each with a small picture (a data URL). Absent in a shell that cannot capture. */
  captureWindows?(): Promise<
    { ok: true; windows: { id: string; name: string; thumb: string }[] } | Fail<'bad_request' | 'unavailable' | 'no_permission'>
  >;
  /** One window of that list as a PNG (base64). */
  captureWindow?(a: {
    id: string;
  }): Promise<
    { ok: true; name: string; png: string } | Fail<'bad_request' | 'unavailable' | 'no_permission' | 'not_found' | 'too_large'>
  >;
  /** Shows an address in a native view over the given rectangle of the window (CSS pixels), or removes the view with null. Absent in a shell that lacks it. */
  preview?(a: { url: string; x: number; y: number; width: number; height: number } | null): Promise<{ ok: true } | Fail<'bad_request'>>;
  /** Which daemon the app talks to: its mode and, in connect mode, the paired device's address. Absent in a shell with one mode. */
  mode?(): Promise<{ ok: true; mode: 'connect' | 'own'; remote: string | null; phone: boolean } | Fail<'bad_request'>>;
  /** Switches the mode; the app relaunches. */
  setMode?(mode: 'connect' | 'own'): Promise<{ ok: true } | Fail<'bad_request'>>;
  /** Opens the one-time QR window for pairing a phone with this app's gateway. `mode().phone` says whether that is possible. */
  phone?(): Promise<{ ok: true } | Fail<'bad_request' | 'cannot_pair'>>;
  /** Pairs with another device's gateway, from its `handline://` link or its address and code; the app relaunches on it. */
  pairRemote?(a: { link?: string; address?: string; code?: string }): Promise<{ ok: true } | Fail<'bad_request' | 'cannot_pair'>>;
  /** The page's zoom factor (1 = 100%), read from the window itself: `outerWidth` lags a full-screen change, so the page cannot tell it from its own size. */
  zoom?(): number;
  /** The window entered or left full screen (the traffic lights are gone in it); called once with the current state on registering. Returns the way to stop listening. */
  onFullScreen?(cb: (full: boolean) => void): () => void;
  /** settings.get through the shell's own daemon connection, for a window that is a paired device and may not read settings. Absent in a shell that lacks it. */
  settingsGet?(a?: {
    agent?: string;
  }): Promise<{ ok: true; result: Methods['settings.get']['result'] } | Fail<'bad_request' | 'refused' | 'unavailable'>>;
  /** settings.set the same way. */
  settingsSet?(a: {
    changes: PageSettings;
    agent?: string;
  }): Promise<{ ok: true; result: Methods['settings.set']['result'] } | Fail<'bad_request' | 'refused' | 'unavailable'>>;
  /** The absolute path of a file or folder dropped from Finder, or '' when it has none. Synchronous: it reads the File, not the disk. Absent in a shell that lacks it. */
  pathOf?(file: File): string;
  /** A notice was clicked. Returns the way to stop listening. */
  onNavigate(cb: (to: NavigateTo) => void): () => void;
}

/**
 * The route a notice's target lands on, or null when there is none to go to (no target, an empty id, the string `None` the daemon
 * emits for a task_done without a task, or a screen this does not know).
 */
export function routeOfTarget(
  target: { screen: string; id: string } | null,
  eventType: string,
  /** The inbox's unread items, when the caller has them: a finished task opens its card, and goes to the task once none is left. */
  unread?: readonly { id: number; task: number | null }[],
): string | null {
  if (!target || !target.id || target.id === 'None') return null;
  const id = encodeURIComponent(target.id);
  switch (target.screen) {
    case 'agent':
      return `#/agents/${id}`;
    case 'task':
      return eventType === 'task_done' ? doneRoute(target.id, unread) : `#/tasks/${id}`;
    case 'account':
      return `#/accounts/${id}`;
    case 'hr':
      return `#/team/hr/${id}`;
    default:
      return null;
  }
}

/** A finished task: its unread inbox card when the caller knows the inbox and has one, the task itself when none is left, `#/inbox` when the inbox is unknown. */
function doneRoute(task: string, unread: readonly { id: number; task: number | null }[] | undefined): string {
  if (!unread) return '#/inbox';
  const card = unread.find((i) => String(i.task) === task);
  return card ? `#/inbox/${card.id}` : `#/tasks/${encodeURIComponent(task)}`;
}
