// The graph view of the Tasks page: one column per agent (the person first), every agent's current task on one "now" row, finished tasks above
// in day bands, queued ones below (the layout is app-core's `columnGraph`). A solid arrow joins a task to what it waits on, a dashed one to the
// next of the same agent. The first click on a card lights its chain and dims the rest, a click on the lit card opens it. Drag pans, ⌘/Ctrl-wheel zooms.
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent } from 'react';
import { CARD_H, CARD_W, GUTTER, HEAD, chainOf, columnGraph, taskRef, type ColumnCard } from '@handline/app-core';
import type { Task } from '@handline/client';
import { goTasks } from '#src/route.ts';
import { Button, Icon } from '#src/ui/index.ts';
import { STATE_GLYPH } from '#src/taskui.tsx';

/** Finished tasks drawn at first, and each "Show more" adds this many. */
const DONE_FIRST = 30;
const DONE_MORE = 100;

const BAND = { done: 'done', now: 'running', next: 'queued' } as const;

function Card({ c, lit, dim, onPick }: { c: ColumnCard; lit: boolean; dim: boolean; onPick: () => void }) {
  return (
    <button
      type="button"
      data-node={c.id}
      data-state={c.state}
      data-band={BAND[c.row]}
      data-lit={lit || undefined}
      data-dim={dim || undefined}
      title={`${taskRef(c.id)} ${c.title} · ${c.holder}`}
      className="absolute flex cursor-pointer flex-col gap-0.5 overflow-hidden rounded-ctl border border-line bg-panel px-2.5 py-1.5 text-left text-body text-ink data-[band=done]:text-ink-3 data-[band=running]:border-working data-[dim]:opacity-35 data-[lit]:border-ink"
      style={{ left: c.x, top: c.y, width: CARD_W, height: CARD_H }}
      onClick={onPick}
    >
      <span className="line-clamp-2 font-medium">{c.title}</span>
      <span className="mt-auto flex items-center gap-1 truncate text-hint text-ink-2">
        <Icon name={STATE_GLYPH[c.state] ?? 'taskPending'} size={12} aria-hidden="true" />
        {taskRef(c.id)} · {c.state} · {c.holder}
      </span>
      {c.waitsOn || c.onYou ? (
        <span className={`flex items-center gap-1 truncate text-hint ${c.onYou ? 'text-wait' : 'text-ink-3'}`}>
          {c.onYou ? <Icon name="waiting" size={12} aria-hidden="true" /> : null}
          {c.waitsOn ?? 'waiting on you'}
        </span>
      ) : null}
    </button>
  );
}

export function TaskColumns({
  tasks,
  now,
  me,
  scopeKey,
}: {
  tasks: Task[];
  now: number;
  me: string;
  /** Changing it centres the view on "now" again. */ scopeKey: string;
}) {
  const [done, setDone] = useState(DONE_FIRST);
  const [zoom, setZoom] = useState(1);
  const [picked, setPicked] = useState<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const g = useMemo(() => columnGraph(tasks, { me, now, doneShown: done }), [tasks, me, now, done]);
  const lit = useMemo(() => (picked === null ? null : chainOf(g.edges, picked)), [g.edges, picked]);
  const nudge = (f: number) => setZoom((k) => Math.min(2, Math.max(0.5, k * f)));

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = Math.max(0, (g.nowY + CARD_H / 2) * zoom - el.clientHeight / 2);
    // Only a new scope or a first draw recentres: a refresh of the tasks must not pull the view away from where the person put it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey, g.cards.length === 0]);
  useEffect(() => {
    const el = scroller.current;
    const wheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      nudge(e.deltaY < 0 ? 1.1 : 1 / 1.1);
    };
    el?.addEventListener('wheel', wheel, { passive: false });
    return () => el?.removeEventListener('wheel', wheel);
  }, []);

  const down = (e: PointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest('button') || e.button !== 0) return;
    const el = e.currentTarget;
    drag.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop };
    el.setPointerCapture(e.pointerId);
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    e.currentTarget.scrollLeft = d.left - (e.clientX - d.x);
    e.currentTarget.scrollTop = d.top - (e.clientY - d.y);
  };
  const pick = (id: number) => (picked === id ? goTasks(id) : setPicked(id));

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Task graph">
      <div
        ref={scroller}
        className="min-h-0 flex-1 cursor-grab overflow-auto active:cursor-grabbing"
        data-testid="task-graph"
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={() => (drag.current = null)}
        onClick={(e) => (e.target === e.currentTarget || !(e.target as HTMLElement).closest('button') ? setPicked(null) : undefined)}
      >
        <div className="sticky top-0 z-10 -mb-10 h-10 bg-canvas" style={{ width: g.width * zoom }}>
          {g.columns.map((c) => (
            <h2
              key={c.key}
              className="absolute m-0 truncate text-label font-semibold text-ink-2"
              style={{ left: c.x * zoom, top: 8, width: CARD_W * zoom, height: HEAD - 8 }}
            >
              {c.label}
              {c.idle ? <span className="ml-2 font-normal text-ink-3">idle</span> : null}
            </h2>
          ))}
        </div>
        <div className="relative origin-top-left" style={{ width: g.width, height: g.height, zoom }}>
          {g.bands.map((b) => (
            <span
              key={b.y}
              className="absolute text-hint font-semibold uppercase tracking-wide text-ink-3"
              style={{ left: 8, top: b.y, width: GUTTER - 16 }}
            >
              {b.label}
            </span>
          ))}
          <span
            className="absolute text-hint font-semibold uppercase tracking-wide text-working"
            style={{ left: 8, top: g.nowY, width: GUTTER - 16 }}
          >
            Now
          </span>
          <div className="absolute inset-x-0 border-t border-working/40" style={{ top: g.nowY - 8 }} aria-hidden="true" />
          <svg className="pointer-events-none absolute inset-0" width={g.width} height={g.height} aria-hidden="true">
            <defs>
              <marker id="tg-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto">
                <path d="M0 0L8 4L0 8z" fill="var(--ink-3)" />
              </marker>
            </defs>
            {g.edges.map((e) => {
              const [a, b] = e.points as [{ x: number; y: number }, { x: number; y: number }];
              const on = lit === null || (lit.has(e.from) && lit.has(e.to));
              return (
                <path
                  key={`${e.kind}-${e.from}-${e.to}`}
                  d={`M${a.x} ${a.y} C${a.x} ${(a.y + b.y) / 2} ${b.x} ${(a.y + b.y) / 2} ${b.x} ${b.y}`}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={e.kind === 'waits' ? 1.5 : 1}
                  strokeDasharray={e.kind === 'next' ? '4 4' : undefined}
                  markerEnd="url(#tg-arrow)"
                  className={`text-ink-3 ${on ? '' : 'opacity-20'}`}
                  data-edge={e.kind}
                />
              );
            })}
          </svg>
          {g.cards.map((c) => (
            <Card
              key={c.id}
              c={c}
              lit={!!lit?.has(c.id) && picked !== null}
              dim={lit !== null && !lit.has(c.id)}
              onPick={() => pick(c.id)}
            />
          ))}
        </div>
      </div>
      <footer className="flex items-center gap-2 px-3 py-2 text-hint text-ink-3">
        <span className="mr-auto">
          Solid arrow: waits on. Dashed: the agent's next. Click a card to light its chain, click it again to open.
        </span>
        {g.moreDone > 0 ? (
          <Button
            label={`Show ${Math.min(DONE_MORE, g.moreDone)} more done`}
            onClick={() => setDone(done + DONE_MORE)}
          >{`Show ${Math.min(DONE_MORE, g.moreDone)} more`}</Button>
        ) : null}
        <Button label="Zoom out" onClick={() => nudge(1 / 1.25)}>
          −
        </Button>
        <Button label="Zoom in" onClick={() => nudge(1.25)}>
          +
        </Button>
        <Button label="Fit" onClick={() => setZoom(1)} />
      </footer>
    </section>
  );
}
