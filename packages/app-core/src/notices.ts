// What the person is told, in three lanes: a decision stays in My turn (`isMyTurn`, a link and no notice), things to know about go to a list
// with an unread count, and the result of what the person just did shows briefly (a toast) and stays in the list. Pure but for `now`.
import type { Event as WireEvent } from '@handline/client';

/** `need` is what waits for the person (a stall, an account out), `fail` a call of theirs that failed; `ok` and `info` are news. */
export type NoticeLevel = 'need' | 'fail' | 'ok' | 'info';
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
  /** Asks again what failed (a failure line's "Try again"). */
  retry?: () => void;
  /** A notice one source keeps current under this key (the update): `Notices.keep` changes it in place. */
  key?: string;
  /** What the kept notice shows beyond its line: a spinner, the step it is on, how far along (0 to 1), and its buttons. */
  live?: Live;
}
export interface Live {
  busy?: boolean;
  /** The restart mark instead of the level's own (a ready update is not a warning). */
  mark?: 'restart';
  step?: string;
  fraction?: number;
  /** `stay`: pressing it leaves the toast up (Details); any other button reads the notice and closes the toast. */
  actions?: { label: string; run: () => void; primary?: boolean; stay?: boolean }[];
}
export interface NoticeAbout {
  agent?: string;
  account?: string;
  proposal?: number;
  task?: number;
  title?: string;
  result?: string;
  /** A finished task the founder gave or answers for; absent when the daemon did not say. */
  fromFounder?: boolean;
}
export type NoticeLane = 'team' | 'system';

/** What an event is about, from the event alone: the task of a task_done, the blocked agent, the account that is out, the settled proposal; null for any other event. */
export function aboutOf(e: Pick<WireEvent, 'type' | 'agent' | 'data'>): NoticeAbout | null {
  const d = (e.data ?? {}) as {
    task?: unknown;
    task_title?: unknown;
    result?: unknown;
    from_founder?: unknown;
    account?: unknown;
    proposal?: { id?: unknown };
  };
  const agent = e.agent ? { agent: e.agent } : {};
  switch (e.type) {
    case 'task_done':
      return {
        ...agent,
        ...(typeof d.task === 'number' ? { task: d.task } : {}),
        ...(typeof d.task_title === 'string' && d.task_title ? { title: d.task_title } : {}),
        ...(typeof d.result === 'string' && d.result ? { result: d.result } : {}),
        ...(typeof d.from_founder === 'boolean' ? { fromFounder: d.from_founder } : {}),
      };
    case 'stall':
      return agent;
    case 'account_out':
      return typeof d.account === 'string' && d.account ? { account: d.account } : null;
    case 'hr.settled':
      return typeof d.proposal?.id === 'number' ? { ...agent, proposal: d.proposal.id } : null;
    default:
      return null;
  }
}

/** The page a notice opens: its proposal, its account, its task, else the agent's chat; null for one that points nowhere (a failure). */
export function targetOf(n: Pick<Notice, 'about'>): string | null {
  const a = n.about;
  if (!a) return null;
  if (a.proposal != null) return `#/team/hr/${a.proposal}`;
  if (a.account) return `#/accounts/${encodeURIComponent(a.account)}`;
  if (a.task != null) return `#/tasks/${a.task}`;
  return a.agent ? `#/agents/${encodeURIComponent(a.agent)}` : null;
}

export const MERGE_MS = 60_000;
/** News goes by itself after this long; a need and a failure stay until the person opens or closes them. */
export const NEWS_MS = 6000;
export const stays = (level: NoticeLevel): boolean => level === 'need' || level === 'fail';
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
/** Events that wait for the person: they stay on screen until opened or closed. */
const NEEDS_YOU = new Set(['stall', 'account_out']);

export class Notices {
  private all: Notice[] = []; // newest first
  /** The toasts on screen: when a news toast runs out (ms since epoch), or null for one that stays until opened or closed. */
  private onScreen = new Map<number, number | null>();
  private left = new Map<number, number>(); // id -> ms left while its clock is stopped
  private holds = new Map<number, Set<Hold>>(); // who keeps a toast's clock stopped: the pointer over it, the keyboard focus in it
  private away = false; // the window is hidden or not focused: no clock runs
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
  push(level: NoticeLevel, text: string, lane: NoticeLane = 'system', about?: NoticeAbout, details?: string, retry?: () => void): Notice {
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
    if (retry) n.retry = retry;
    n.count++;
    n.at = at;
    n.read = false;
    this.all = [n, ...this.all.filter((x) => x !== n)].slice(0, MAX_LIST);
    this.left.delete(n.id);
    this.onScreen.set(n.id, stays(level) ? null : at + NEWS_MS);
    this.sync();
    this.emit();
    return n;
  }

  /**
   * The notice a source keeps current under `key`: the first call adds it, later calls change its line, level and `live` part in place, so
   * the list has one row for it. `show` puts its toast on screen (it stays until closed); without it the row stays in the list, unread.
   */
  keep(key: string, level: NoticeLevel, text: string, live: Live, show: boolean): Notice {
    const at = this.now();
    const old = this.all.find((x) => x.key === key);
    // A new object each time: a toast already on screen is drawn again with the new title, line and buttons.
    const n: Notice = old
      ? { ...old, level, text, live, at, read: false }
      : { id: this.nextId++, level, text, at, count: 1, read: false, lane: 'system', key, live };
    this.all = [n, ...this.all.filter((x) => x !== old)].slice(0, MAX_LIST);
    this.left.delete(n.id);
    if (show) this.onScreen.set(n.id, null);
    else this.onScreen.delete(n.id);
    this.emit();
    return n;
  }
  /** Takes a kept notice away, from the screen and the list. */
  drop(key: string): void {
    const n = this.all.find((x) => x.key === key);
    if (!n) return;
    this.all = this.all.filter((x) => x !== n);
    this.close(n.id);
  }

  /** Stops or starts each news clock by who holds it; a stopped one remembers what it had left. */
  private sync(): void {
    const at = this.now();
    for (const [id, until] of this.onScreen) {
      if (until === null) continue;
      const stopped = this.away || (this.holds.get(id)?.size ?? 0) > 0;
      if (stopped && !this.left.has(id)) this.left.set(id, Math.max(until - at, 0));
      else if (!stopped && this.left.has(id)) {
        this.onScreen.set(id, at + this.left.get(id)!);
        this.left.delete(id);
      }
      const next = this.onScreen.get(id)!;
      if (!stopped) this.expireAt(next);
    }
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
    if (text)
      this.push(NEEDS_YOU.has(e.type) ? 'need' : 'info', text, TEAM_EVENTS.has(e.type) ? 'team' : 'system', aboutOf(e) ?? undefined);
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
  /** The person opened what the notice points to: it is read and its toast is gone. */
  open(id: number): void {
    const n = this.all.find((x) => x.id === id);
    if (n) n.read = true;
    this.close(id);
  }
  /** Closes one toast (the X); the list keeps it, still unread. */
  close(id: number): void {
    this.onScreen.delete(id);
    this.left.delete(id);
    this.holds.delete(id);
    this.emit();
  }

  /** The notices still on screen, newest first, at most three; the older ones live on in the list. When there are more, news gives way before what needs the person. */
  toasts(): Notice[] {
    const at = this.now();
    const live = this.all.filter(
      (n) => this.onScreen.has(n.id) && (this.onScreen.get(n.id) === null || this.left.has(n.id) || this.onScreen.get(n.id)! > at),
    );
    const room = live.filter((n) => stays(n.level)).slice(0, MAX_TOASTS).length;
    const news = live.filter((n) => !stays(n.level)).slice(0, MAX_TOASTS - room);
    return live.filter(
      (n) =>
        news.includes(n) ||
        live
          .filter((x) => stays(x.level))
          .slice(0, MAX_TOASTS)
          .includes(n),
    );
  }
  /** When the soonest news toast on screen runs out (ms since epoch), or null when none will: a screen draws again then. */
  nextExpiry(): number | null {
    const times = this.toasts()
      .filter((n) => this.onScreen.get(n.id) !== null && !this.left.has(n.id))
      .map((n) => this.onScreen.get(n.id)!);
    return times.length ? Math.min(...times) : null;
  }
  /** The pointer is over a toast, or the keyboard focus is in it: its clock stops until both are gone. */
  hold(id: number, by: Hold = 'pointer'): void {
    const set = this.holds.get(id) ?? new Set<Hold>();
    set.add(by);
    this.holds.set(id, set);
    this.sync();
  }
  release(id: number, by: Hold = 'pointer'): void {
    this.holds.get(id)?.delete(by);
    this.sync();
  }
  /** The window is hidden or not focused: no clock runs, so nothing goes unseen. */
  setAway(away: boolean): void {
    if (this.away === away) return;
    this.away = away;
    this.sync(); // nothing on screen changes yet: a clock that stops leaves its toast up, so no screen needs to draw again
  }
  /** Hides the toasts that are news or a failure (the person went elsewhere); what needs them stays, and the list keeps all. */
  dismissToasts(): void {
    for (const n of this.all) if (n.level !== 'need') this.close(n.id);
  }
  /** The newest failure still on screen, for a screen that shows one line. */
  latestFailure(): string | null {
    return this.toasts().find((n) => n.level === 'fail')?.text ?? null;
  }
}

type Hold = 'pointer' | 'focus';
