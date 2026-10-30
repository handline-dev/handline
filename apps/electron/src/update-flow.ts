// What the person is told about an update, in one place: preparing, ready, later, failed. The Updater (a build that changed under a running app) and the
// Follower (GitHub's main moved) both report here; the page draws it as one notice, the menu-bar menu says the same words.

export type UpdatePhase = 'idle' | 'preparing' | 'ready' | 'later' | 'failed';
export interface UpdateView {
  phase: UpdatePhase;
  /** While preparing: "Building (2 of 3)"; empty otherwise. */
  step: string;
  /** While preparing: how far along, 0 to 1. */
  fraction: number;
  /** When failed: the one-line reason. */
  reason: string;
}
export type UpdateAct = 'restart' | 'later' | 'retry' | 'details';
export const UPDATE_ACTS: readonly UpdateAct[] = ['restart', 'later', 'retry', 'details'];

/** A Later is asked about again after this long (an update that is still waiting is raised once a day, never as a second window). */
export const REMIND_MS = 24 * 3_600_000;

export class UpdateFlow {
  view: UpdateView = { phase: 'idle', step: '', fraction: 0, reason: '' };
  /** The command output behind a failure, for "Details". */
  log = '';
  private listeners: (() => void)[] = [];
  private restartFn: () => void = () => undefined;
  private retryFn: () => void = () => undefined;
  private laterAt = 0;
  private now: () => number;
  constructor(now: () => number = Date.now) {
    this.now = now;
  }

  onChange(f: () => void): void {
    this.listeners.push(f);
  }
  private set(view: UpdateView): void {
    this.view = view;
    for (const f of this.listeners) f();
  }

  idle(): void {
    if (this.view.phase !== 'idle') this.set({ phase: 'idle', step: '', fraction: 0, reason: '' });
  }
  preparing(step: string, fraction: number): void {
    this.set({ phase: 'preparing', step, fraction, reason: '' });
  }
  /** The update is built: "Restart now" runs `restart`. */
  ready(restart: () => void): void {
    this.restartFn = restart;
    this.set({ phase: 'ready', step: '', fraction: 0, reason: '' });
  }
  failed(reason: string, retry: () => void, log = ''): void {
    this.retryFn = retry;
    this.log = log;
    this.set({ phase: 'failed', step: '', fraction: 0, reason });
  }
  /** "Later": the toast goes and the bell keeps the row. Only a ready update can be put off. */
  later(): void {
    if (this.view.phase !== 'ready') return;
    this.laterAt = this.now();
    this.set({ ...this.view, phase: 'later' });
  }
  /** A put-off update comes back once a day: true when it was raised again. */
  remind(): boolean {
    if (this.view.phase !== 'later' || this.now() - this.laterAt < REMIND_MS) return false;
    this.set({ ...this.view, phase: 'ready' });
    return true;
  }
  /** What a button of the notice or the menu does. `details` is the owner's: it opens the log. */
  act(what: UpdateAct): void {
    if (what === 'later') this.later();
    else if (what === 'restart' && (this.view.phase === 'ready' || this.view.phase === 'later')) this.restartFn();
    else if (what === 'retry' && this.view.phase === 'failed') this.retryFn();
  }
}
