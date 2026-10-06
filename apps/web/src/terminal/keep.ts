import type { Disposable, PaneTerm } from './core.ts';

/** A terminal pane that was hidden: its xterm instance, still holding the last screen, waiting for a quick switch back. */
export interface Kept {
  stage: HTMLElement;
  pane: PaneTerm;
  quiet: Disposable;
  /** Where title notices go; the tab that is showing the terminal now sets it. */
  title: { to: (t: string) => void };
}

export const KEEP_MS = 30_000;
const kept = new Map<string, { k: Kept; timer: ReturnType<typeof setTimeout> }>();

const dispose = (k: Kept) => {
  k.quiet.dispose();
  k.pane.dispose();
  k.stage.remove();
};

/** Takes a hidden terminal back, or undefined once it has expired. */
export function reclaim(agent: string): Kept | undefined {
  const e = kept.get(agent);
  if (!e) return undefined;
  clearTimeout(e.timer);
  kept.delete(agent);
  return e.k;
}
/** Keeps a hidden terminal for `ms`, then disposes it. The daemon was already told to detach: this is the screen only. */
export function keep(agent: string, k: Kept, ms = KEEP_MS): void {
  const old = reclaim(agent);
  if (old && old !== k) dispose(old);
  kept.set(agent, {
    k,
    timer: setTimeout(() => {
      kept.delete(agent);
      dispose(k);
    }, ms),
  });
}
export function dropKept(): void {
  for (const agent of [...kept.keys()]) {
    const k = reclaim(agent);
    if (k) dispose(k);
  }
}
