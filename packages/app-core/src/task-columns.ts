// The task graph by agent as numbers, no drawing: one column per agent (the person first), the current task of each on one "now" row, finished
// tasks above it in day bands, queued ones below, and the arrows: solid where a task waits on another, dashed from a task to the next in its column.
import type { Task } from '@handline/client';
import { CLOSED } from '#src/task-table.ts';
import { isPerson } from '#src/chat.ts';
import { waitReason } from '#src/task-graph.ts';

export const CARD_W = 208;
export const CARD_H = 72;
export const GAP_X = 24;
export const GAP_Y = 16;
/** Left strip for the band names, and the strip on top for the column names. */
export const GUTTER = 88;
export const HEAD = 40;
const BAND_HEAD = 24;
const DAY = 86_400_000;

export interface ColumnCard {
  id: number;
  title: string;
  state: string;
  /** Whom the task is with, short: the last part of the agent's name. */
  holder: string;
  /** Why it waits, in the shared words; null when it does not. */
  waitsOn: string | null;
  /** It waits on the person looking, or sits in the person's column. */
  onYou: boolean;
  row: 'done' | 'now' | 'next';
  column: string;
  x: number;
  y: number;
}
export interface ColumnEdge {
  from: number;
  to: number;
  /** `waits`: `to` waits on `from`, across columns or not; `next`: the next task of the same agent. */
  kind: 'waits' | 'next';
  points: { x: number; y: number }[];
}
export interface ColumnGraph {
  columns: { key: string; label: string; x: number; /** An agent with nothing running. */ idle: boolean }[];
  cards: ColumnCard[];
  edges: ColumnEdge[];
  /** Day bands of the finished tasks, oldest at the top. */
  bands: { label: string; y: number }[];
  /** Top of the "now" row. */
  nowY: number;
  width: number;
  height: number;
  /** Finished tasks left out by the limit. */
  moreDone: number;
}

const closedAt = (t: Task) => Date.parse(t.done ?? t.created);
const startOfDay = (ms: number) => new Date(ms).setHours(0, 0, 0, 0);
const dayLabel = (day: number, now: number) => {
  const ago = Math.round((startOfDay(now) - day) / DAY);
  return ago <= 0 ? 'Today' : ago === 1 ? 'Yesterday' : `${ago} days ago`;
};
const shortOf = (name: string) => name.slice(name.lastIndexOf('.') + 1);

/** The graph of `tasks`; `doneShown` caps the finished tasks drawn, newest first. */
export function columnGraph(tasks: readonly Task[], opts: { me: string; now: number; doneShown?: number }): ColumnGraph {
  const { me, now } = opts;
  const colOf = (t: Task) => (isPerson(t.to, me) ? 'you' : t.to);
  const closed = tasks.filter((t) => CLOSED.includes(t.state)).sort((a, b) => closedAt(b) - closedAt(a));
  const shownDone = closed.slice(0, opts.doneShown ?? 30);
  const open = tasks.filter((t) => !CLOSED.includes(t.state));
  const openIds = new Set(open.map((t) => t.id));
  const keys = [...new Set([...open, ...shownDone].map(colOf))].filter((k) => k !== 'you').sort();
  keys.unshift('you');
  const colX = (k: string) => GUTTER + keys.indexOf(k) * (CARD_W + GAP_X);

  const byPriority = (a: Task, b: Task) => (a.priority ?? 2) - (b.priority ?? 2) || a.id - b.id;
  const nowOf = new Map<string, Task>();
  const queue = new Map<string, Task[]>();
  for (const t of [...open].sort(byPriority)) {
    const k = colOf(t);
    if (k !== 'you' && t.state === 'doing' && !nowOf.has(k)) nowOf.set(k, t);
    else queue.set(k, [...(queue.get(k) ?? []), t]);
  }

  // Day bands, oldest first; a band is as tall as its fullest column, and a column's cards sit at its bottom, newest lowest.
  const days = [...new Set(shownDone.map((t) => startOfDay(closedAt(t))))].sort((a, b) => a - b);
  const rowsOf = (day: number) =>
    Math.max(...keys.map((k) => shownDone.filter((t) => colOf(t) === k && startOfDay(closedAt(t)) === day).length));
  const bands: ColumnGraph['bands'] = [];
  const dayTop = new Map<number, number>();
  let y = HEAD;
  for (const day of days) {
    bands.push({ label: dayLabel(day, now), y });
    dayTop.set(day, y + BAND_HEAD);
    y += BAND_HEAD + rowsOf(day) * (CARD_H + GAP_Y);
  }
  const nowY = y;

  const card = (t: Task, row: ColumnCard['row'], cx: number, cy: number): ColumnCard => {
    const waitsOn = waitReason(t, me, openIds);
    return {
      id: t.id,
      title: t.title,
      state: t.state,
      holder: isPerson(t.to, me) ? 'you' : shortOf(t.to),
      waitsOn,
      onYou: isPerson(t.to, me) || !!t.requests?.some((r) => isPerson(r.to, me) && ['queued', 'typed', 'landed'].includes(r.state)),
      row,
      column: colOf(t),
      x: cx,
      y: cy,
    };
  };
  const cards: ColumnCard[] = [];
  for (const day of days) {
    for (const k of keys) {
      const col = shownDone.filter((t) => colOf(t) === k && startOfDay(closedAt(t)) === day).reverse();
      const bottom = dayTop.get(day)! + rowsOf(day) * (CARD_H + GAP_Y);
      col.forEach((t, i) => cards.push(card(t, 'done', colX(k), bottom - (col.length - i) * (CARD_H + GAP_Y))));
    }
  }
  for (const k of keys) {
    const cur = nowOf.get(k);
    if (cur) cards.push(card(cur, 'now', colX(k), nowY));
    (queue.get(k) ?? []).forEach((t, i) => cards.push(card(t, 'next', colX(k), nowY + (i + 1) * (CARD_H + GAP_Y))));
  }

  const at = new Map(cards.map((c) => [c.id, c]));
  const edges: ColumnEdge[] = [];
  for (const t of tasks) {
    const to = at.get(t.id);
    if (!to) continue;
    for (const h of t.blocked_by ?? []) {
      const from = at.get(h);
      if (!from || h === t.id || !openIds.has(h)) continue;
      const down = from.y <= to.y;
      edges.push({
        from: h,
        to: t.id,
        kind: 'waits',
        points: [
          { x: from.x + CARD_W / 2, y: from.y + (down ? CARD_H : 0) },
          { x: to.x + CARD_W / 2, y: to.y + (down ? 0 : CARD_H) },
        ],
      });
    }
  }
  for (const k of keys) {
    const line = cards.filter((c) => c.column === k && c.row !== 'done');
    line.slice(1).forEach((c, i) => {
      const p = line[i]!;
      edges.push({
        from: p.id,
        to: c.id,
        kind: 'next',
        points: [
          { x: p.x + CARD_W / 2, y: p.y + CARD_H },
          { x: c.x + CARD_W / 2, y: c.y },
        ],
      });
    });
  }

  const lowest = Math.max(nowY + CARD_H, ...cards.map((c) => c.y + CARD_H));
  return {
    columns: keys.map((k) => ({ key: k, label: k === 'you' ? 'You' : shortOf(k), x: colX(k), idle: k !== 'you' && !nowOf.has(k) })),
    cards,
    edges,
    bands,
    nowY,
    width: GUTTER + keys.length * (CARD_W + GAP_X),
    height: lowest + GAP_Y,
    moreDone: closed.length - shownDone.length,
  };
}

/** The tasks joined to `id` by "waits on", whichever way, and `id` itself: what a click lights. */
export function chainOf(edges: readonly ColumnEdge[], id: number): Set<number> {
  const lit = new Set([id]);
  for (let grew = true; grew;) {
    grew = false;
    for (const e of edges) {
      if (e.kind !== 'waits') continue;
      if (lit.has(e.from) !== lit.has(e.to)) {
        lit.add(e.from).add(e.to);
        grew = true;
      }
    }
  }
  return lit;
}
