// The surface between the web app and the desktop shell (docs/electron-bridge.md): types and the one table from an event's target to a route.
// Pure, no DOM: the shell's preload and the web app both import it.

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
  }): Promise<{ ok: true; how: 'opened' | 'revealed' } | Fail<'bad_request' | 'not_found' | 'unavailable'>>;
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
  /** A notice was clicked. Returns the way to stop listening. */
  onNavigate(cb: (to: NavigateTo) => void): () => void;
}

/**
 * The route a notice's target lands on, or null when there is none to go to (no target, an empty id, the string `None` the daemon
 * emits for a task_done without a task, or a screen this does not know).
 */
export function routeOfTarget(target: { screen: string; id: string } | null, eventType: string): string | null {
  if (!target || !target.id || target.id === 'None') return null;
  const id = encodeURIComponent(target.id);
  switch (target.screen) {
    case 'agent':
      return `#/agents/${id}`;
    case 'task':
      return eventType === 'task_done' ? '#/inbox' : `#/tasks/${id}`;
    case 'account':
      return `#/accounts/${id}`;
    default:
      return null;
  }
}
