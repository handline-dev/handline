// The surface between the web app and the desktop shell (docs/electron-bridge.md): types and the one table from an event's target to a route.
// Pure, no DOM: the shell's preload and the web app both import it.

import type { Methods, SettingsChanges } from '@handline/client';

/** The settings the Settings page and the status bar's resource area edit, the only keys the shell sets on its behalf (the other daemon settings are not the page's to change). */
export const PAGE_SETTINGS = [
  'active_cap',
  'park_timeout_s',
  'handoff_note',
  'handoff_prompt',
  'ignore_cache_expiry',
  'notice_digest_s',
  'bg_notice_s',
  'bg_grace_s',
  'bg_check_s',
  'push_prompt_asked',
  'push_task_done',
  'push_stall',
  'push_account_out',
  'pacing',
  'pace_target_pct',
  'pace_burst_pct',
  // the status bar's resource area
  'host_active_cap',
  'keep_awake',
  'load_per_core_pct',
  'ram_free_min_pct',
  'heavy_slots',
] as const satisfies readonly (keyof SettingsChanges)[];
export type PageSettings = Pick<SettingsChanges, (typeof PAGE_SETTINGS)[number]>;

/** What every call returns when it does not succeed: calls never throw across the window's edge. */
export type Fail<C extends string> = { ok: false; code: C; message: string };

/** A clicked notice: the notifications event's type and the `target` the daemon put on it. */
export interface NavigateTo {
  screen: string;
  id: string;
  event: string;
}

/** Where an update is: preparing (with its step and how far along), ready, put off until later, or failed (with the reason). */
export interface ShellUpdate {
  phase: 'idle' | 'preparing' | 'ready' | 'later' | 'failed';
  step: string;
  fraction: number;
  reason: string;
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
  /** Copy a dropped file or folder into the agent's worktree and give the copy's path. Absent in a shell that lacks it. */
  bringIn?(a: { path: string; agent: string }): Promise<{ ok: true; path: string } | Fail<'bad_request' | 'not_found' | 'unavailable'>>;
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
  }): Promise<{ ok: true; name: string; png: string } | Fail<'bad_request' | 'unavailable' | 'no_permission' | 'not_found' | 'too_large'>>;
  /** Shows an address in a native view over the given rectangle of the window (CSS pixels), or removes the view with null. Absent in a shell that lacks it. */
  preview?(a: { url: string; x: number; y: number; width: number; height: number } | null): Promise<{ ok: true } | Fail<'bad_request'>>;
  /** The preview as it is on screen, a PNG (base64). */
  capturePreview?(): Promise<{ ok: true; png: string } | Fail<'bad_request' | 'unavailable'>>;
  /** The booted iOS simulators and the Android devices adb sees, and whether idb is installed (a simulator is driven through it). */
  deviceList?(): Promise<
    { ok: true; devices: { kind: 'android' | 'ios'; id: string; name: string }[]; idb: boolean } | Fail<'bad_request' | 'unavailable'>
  >;
  /** The picture of one running Android emulator or device (adb) or iOS simulator (simctl) as a PNG (base64); `unavailable` says plainly why there is none. */
  deviceShot?(a: { kind: 'android' | 'ios'; id: string }): Promise<{ ok: true; png: string } | Fail<'bad_request' | 'unavailable'>>;
  /** A tap on that device at a place in its last picture, as fractions (0 to 1) of its width and height. */
  deviceTap?(a: { kind: 'android' | 'ios'; id: string; x: number; y: number }): Promise<{ ok: true } | Fail<'bad_request' | 'unavailable'>>;
  /** A drag on that device between two places of its last picture, as fractions, over `ms` milliseconds. */
  deviceSwipe?(a: {
    kind: 'android' | 'ios';
    id: string;
    x1: number;
    y1: number;
    x2: number;
    y2: number;
    ms?: number;
  }): Promise<{ ok: true } | Fail<'bad_request' | 'unavailable'>>;
  /** One line typed on that device. */
  deviceText?(a: { kind: 'android' | 'ios'; id: string; text: string }): Promise<{ ok: true } | Fail<'bad_request' | 'unavailable'>>;
  /** Which daemon the app talks to: its mode and, in connect mode, the paired device's address. Absent in a shell with one mode. */
  mode?(): Promise<{ ok: true; mode: 'connect' | 'own'; remote: string | null; phone: boolean } | Fail<'bad_request'>>;
  /** Switches the mode; the app relaunches. */
  setMode?(mode: 'connect' | 'own'): Promise<{ ok: true } | Fail<'bad_request'>>;
  /** The follow-main build's commit and whether a newer main is waiting; `following` is false in any other build. */
  follow?(): Promise<
    | { ok: true; following: boolean; sha: string; state: 'current' | 'available' | 'updating' | 'ready' | 'failed'; next: string | null }
    | Fail<'bad_request'>
  >;
  /** The build the app is (its git sha, null when it has none) and whether the app can move the daemon onto it; the page compares the sha with the daemon's. */
  build?(): Promise<{ ok: true; sha: string | null; canRestart: boolean } | Fail<'bad_request'>>;
  /** Builds and restarts the daemon onto the app's build, as Update does; `cannot_restart` says why not. */
  restartDaemon?(): Promise<{ ok: true } | Fail<'bad_request' | 'cannot_restart'>>;
  /** The `hl` command an installed app carries: whether it has one, and whether it is linked into ~/.local/bin. */
  cli?(): Promise<{ ok: true; bundled: boolean; linked: boolean; link: string } | Fail<'bad_request'>>;
  /** Links the carried `hl` into ~/.local/bin; `cannot_link` says why not (another file is there). */
  linkCli?(): Promise<{ ok: true } | Fail<'bad_request' | 'cannot_link'>>;
  /** Looks at GitHub's main now; the answer is `follow()` again. */
  checkUpdate?(): Promise<{ ok: true } | Fail<'bad_request'>>;
  /** The update notice: where the update is, and its buttons. `onUpdate` is called now with the last state and on every change. */
  update?(): Promise<({ ok: true } & ShellUpdate) | Fail<'bad_request'>>;
  updateAct?(what: 'restart' | 'later' | 'retry' | 'details'): Promise<{ ok: true } | Fail<'bad_request'>>;
  onUpdate?(cb: (v: ShellUpdate) => void): () => void;
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
export function routeOfTarget(target: { screen: string; id: string } | null): string | null {
  // Pages with no id: the tray's Inbox and Settings lines.
  if (target?.screen === 'inbox') return '#/my-turn';
  if (target?.screen === 'settings') return '#/settings';
  if (!target || !target.id || target.id === 'None') return null;
  const id = encodeURIComponent(target.id);
  switch (target.screen) {
    case 'agent':
      return `#/agents/${id}`;
    case 'task':
      return `#/tasks/${id}`;
    case 'account':
      return `#/accounts/${id}`;
    case 'hr':
      return `#/team/hr/${id}`;
    default:
      return null;
  }
}
