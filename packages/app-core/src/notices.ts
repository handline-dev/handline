// What the person is told, in three lanes: a decision stays in My turn (`isMyTurn`, a link and no notice), things to know about go to a list
// with an unread count, and the result of what the person just did shows briefly (a toast) and stays in the list. Pure but for `now`.
import type { Event as WireEvent } from '@handline/client';

export type NoticeLevel = 'ok' | 'fail' | 'info';
export interface Notice {
  id: number;
  level: NoticeLevel;
  text: string;
  /** When it last came, ms since epoch. */
  at: number;
  /** How many times the same text came within `MERGE_MS`. */
  count: number;
  read: boolean;
}

export const MERGE_MS = 60_000;
export const TOAST_MS = { ok: 3000, fail: 8000, info: 3000 } as const;
export const MAX_TOASTS = 3;
export const MAX_LIST = 100;

/** Events that ask for a decision: they are counted in My turn, so the bell only links to it. */
export const isMyTurn = (e: Pick<WireEvent, 'topic' | 'type'>): boolean =>
  (e.topic === 'notifications' && e.type === 'prompt_asked') || (e.topic === 'hr' && e.type === 'hr.raised');

/** The line for an event worth knowing about, or null for one that is not (a decision, or a topic the list does not carry). */
export function eventText(e: Pick<WireEvent, 'topic' | 'type' | 'agent' | 'title' | 'data'>): string | null {
  if (isMyTurn(e)) return null;
  const d = e.data as { task?: unknown; account?: unknown };
  switch (e.type) {
    case 'task_done':
      return e.title ?? `${e.agent ? `${e.agent}: ` : ''}task ${d.task ?? ''} is done`.trim();
    case 'stall':
      return e.title ?? `${e.agent ?? 'An agent'} stalled`;
    case 'account_out':
      return e.title ?? `Account ${d.account ?? ''} is out`.trim();
    case 'hr.settled':
      return e.title ?? 'A proposal was settled';
    default:
      return null;
  }
}

export class Notices {
  private all: Notice[] = []; // newest first
  private shownUntil = new Map<number, number>();
  private paused = new Map<number, number>(); // id -> ms left when the pointer came
  private seen = new Set<number>(); // wire cursors already taken, so a feed read and a live event do not count twice
  private nextId = 1;
  private listeners = new Set<() => void>();
  private now: () => number;
  constructor(now: () => number = Date.now) {
    this.now = now;
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  private emit() {
    for (const fn of this.listeners) fn();
  }

  /** Adds a notice, or counts it again when the same text came within a minute. It is shown at once. */
  push(level: NoticeLevel, text: string): Notice {
    const at = this.now();
    const same = this.all.find((n) => n.level === level && n.text === text && at - n.at < MERGE_MS);
    const n = same ?? { id: this.nextId++, level, text, at, count: 0, read: false };
    n.count++;
    n.at = at;
    n.read = false;
    this.all = [n, ...this.all.filter((x) => x !== n)].slice(0, MAX_LIST);
    this.shownUntil.set(n.id, at + TOAST_MS[level]);
    this.paused.delete(n.id);
    this.emit();
    return n;
  }

  /** Takes an event from the stream or the feed once; a decision or an unknown kind is skipped. */
  takeEvent(e: WireEvent): void {
    if (this.seen.has(e.cursor)) return;
    this.seen.add(e.cursor);
    const text = eventText(e);
    if (text) this.push('info', text);
  }

  /** The list, newest first. */
  list(): readonly Notice[] {
    return this.all;
  }
  unread(): number {
    return this.all.filter((n) => !n.read).length;
  }
  markAllRead(): void {
    for (const n of this.all) n.read = true;
    this.emit();
  }

  /** The notices still on screen, newest first, at most three; the older ones live on in the list. A notice a pointer holds does not run out. */
  toasts(): Notice[] {
    const at = this.now();
    return this.all.filter((n) => this.paused.has(n.id) || (this.shownUntil.get(n.id) ?? 0) > at).slice(0, MAX_TOASTS);
  }
  /** The pointer is over a toast: it stays until the pointer leaves, then runs its time out again. */
  hold(id: number): void {
    const left = (this.shownUntil.get(id) ?? 0) - this.now();
    if (!this.paused.has(id)) this.paused.set(id, Math.max(left, 0));
  }
  release(id: number): void {
    const left = this.paused.get(id);
    if (left === undefined) return;
    this.paused.delete(id);
    this.shownUntil.set(id, this.now() + left);
  }
  /** Hides every toast (the person closed them); the list keeps them. */
  dismissToasts(): void {
    for (const n of this.all) this.shownUntil.delete(n.id);
    this.paused.clear();
    this.emit();
  }
  /** The newest failure still on screen, for a screen that shows one line. */
  latestFailure(): string | null {
    return this.toasts().find((n) => n.level === 'fail')?.text ?? null;
  }
}
