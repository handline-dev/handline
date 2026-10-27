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
  /** Where the list files it: `team` for what agents did, `system` for the rest (results of what the person did, accounts). */
  lane: NoticeLane;
  /** What a finished task adds to the line: who closed it, which task, its title and the first line of its evidence. */
  about?: NoticeAbout;
  /** The daemon's own words behind a failure line, shown only behind "Details". */
  details?: string;
}
export interface NoticeAbout {
  agent?: string;
  task?: number;
  title?: string;
  result?: string;
}
export type NoticeLane = 'team' | 'system';

/** The task a task_done event is about, from the event alone; null for any other event. */
export function aboutOf(e: Pick<WireEvent, 'type' | 'agent' | 'data'>): NoticeAbout | null {
  if (e.type !== 'task_done') return null;
  const d = (e.data ?? {}) as { task?: unknown; task_title?: unknown; result?: unknown };
  return {
    ...(e.agent ? { agent: e.agent } : {}),
    ...(typeof d.task === 'number' ? { task: d.task } : {}),
    ...(typeof d.task_title === 'string' && d.task_title ? { title: d.task_title } : {}),
    ...(typeof d.result === 'string' && d.result ? { result: d.result } : {}),
  };
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
  const d = e.data as { task?: unknown; task_title?: unknown; account?: unknown };
  switch (e.type) {
    case 'task_done':
      return typeof d.task === 'number'
        ? `T${d.task} done${typeof d.task_title === 'string' ? ` · ${d.task_title}` : ''}`
        : (e.title ?? 'A task is done');
    case 'stall':
      return e.title ?? `${e.agent ?? 'An agent'} is blocked`;
    case 'account_out':
      return e.title ?? `Account ${d.account ?? ''} is out of usage`.replace('  ', ' ');
    case 'hr.settled':
      return e.title ?? 'A proposal was settled';
    default:
      return null;
  }
}

const TEAM_EVENTS = new Set(['task_done', 'stall', 'hr.settled']);

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
  push(level: NoticeLevel, text: string, lane: NoticeLane = 'system', about?: NoticeAbout, details?: string): Notice {
    const at = this.now();
    const same = this.all.find((n) => n.level === level && n.text === text && n.lane === lane && at - n.at < MERGE_MS);
    const n = same ?? {
      id: this.nextId++,
      level,
      text,
      at,
      count: 0,
      read: false,
      lane,
      ...(about ? { about } : {}),
      ...(details ? { details } : {}),
    };
    n.count++;
    n.at = at;
    n.read = false;
    this.all = [n, ...this.all.filter((x) => x !== n)].slice(0, MAX_LIST);
    this.shownUntil.set(n.id, at + TOAST_MS[level]);
    this.paused.delete(n.id);
    this.emit();
    this.expireAt(this.shownUntil.get(n.id)!);
    return n;
  }

  /** Tells the listeners once the toast that runs out at `when` has: what is on screen depends on the clock, and nothing else would draw again. */
  private expireAt(when: number): void {
    const timer: unknown = setTimeout(() => this.emit(), Math.max(when - this.now(), 0) + 1);
    // Under Node a pending timer would keep a finished process (a test) alive; a browser's handle is a number and has no unref.
    if (typeof timer === 'object' && timer !== null && 'unref' in timer && typeof timer.unref === 'function') timer.unref();
  }

  /** Takes an event from the stream or the feed once; a decision or an unknown kind is skipped. */
  takeEvent(e: WireEvent): void {
    if (this.seen.has(e.cursor)) return;
    this.seen.add(e.cursor);
    const text = eventText(e);
    if (text) this.push('info', text, TEAM_EVENTS.has(e.type) ? 'team' : 'system', aboutOf(e) ?? undefined);
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
  /** When the soonest toast on screen runs out (ms since epoch), or null when none will: a screen draws again then. */
  nextExpiry(): number | null {
    const times = this.toasts()
      .filter((n) => !this.paused.has(n.id))
      .map((n) => this.shownUntil.get(n.id) ?? 0);
    return times.length ? Math.min(...times) : null;
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
    this.expireAt(this.shownUntil.get(id)!);
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
