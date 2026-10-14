// The graph page: tasks as boxes, an arrow from the task that holds to the task that waits (the layout is app-core's `taskGraph`).
// Drag to pan, wheel or the buttons to zoom, click a box to open the task.
import { useMemo, useRef, useState, type PointerEvent, type WheelEvent } from 'react';
import { taskGraph, taskRef, type GraphEdge } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { useTasks } from '@handline/app-data';
import { toneOf } from '@handline/app-ui';
import { goTasks } from './route.ts';
import { Button } from './ui/index.ts';

const path = (e: GraphEdge) => e.points.map((p, i) => `${i ? 'L' : 'M'}${p.x} ${p.y}`).join(' ');
const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const TONE_CLASS = { run: 'text-working', wait: 'text-wait', block: 'text-bad', idle: 'text-ink-2' } as const;

export function TaskGraphPage() {
  const me = useStore().me;
  const q = useTasks({});
  const g = useMemo(() => taskGraph(q.data?.items ?? [], { me }), [q.data, me]);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ x: number; y: number } | null>(null);
  const zoom = (f: number) => setView((v) => ({ ...v, k: Math.min(3, Math.max(0.3, v.k * f)) }));
  const down = (e: PointerEvent) => {
    drag.current = { x: e.clientX - view.x, y: e.clientY - view.y };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e: PointerEvent) => {
    const d = drag.current;
    if (d) setView((v) => ({ ...v, x: e.clientX - d.x, y: e.clientY - d.y }));
  };
  const wheel = (e: WheelEvent) => zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1);
  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Task graph">
      <header className="flex items-center gap-2 border-b border-line p-2">
        <Button icon="back" label="Tasks" onClick={() => goTasks()} />
        <strong className="mr-auto">Graph</strong>
        <Button label="−" title="Zoom out" onClick={() => zoom(1 / 1.25)} />
        <Button label="+" title="Zoom in" onClick={() => zoom(1.25)} />
        <Button label="Reset" onClick={() => setView({ x: 0, y: 0, k: 1 })} />
      </header>
      {g.nodes.length === 0 ? (
        <p className="p-4 text-ink-3">No open tasks.</p>
      ) : (
        <svg
          className="min-h-0 flex-1 cursor-grab touch-none select-none"
          data-testid="task-graph"
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={() => (drag.current = null)}
          onWheel={wheel}
        >
          <defs>
            <marker id="tg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 0 L10 5 L0 10 z" fill="currentColor" />
            </marker>
          </defs>
          <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`} className="text-ink-3">
            {g.edges.map((e) => (
              <g key={`${e.from}-${e.to}`} data-edge={`${e.from}-${e.to}`}>
                <path d={path(e)} fill="none" stroke="currentColor" markerEnd="url(#tg-arrow)" />
                <text
                  x={e.points[Math.floor(e.points.length / 2)]!.x + 6}
                  y={e.points[Math.floor(e.points.length / 2)]!.y}
                  fontSize="10"
                  fill="currentColor"
                >
                  {e.label}
                </text>
              </g>
            ))}
            {g.nodes.map((n) => (
              <g
                key={n.id}
                data-node={n.id}
                data-state={n.state}
                className={`cursor-pointer ${TONE_CLASS[toneOf(n.state)]}`}
                transform={`translate(${n.x} ${n.y})`}
                onClick={() => goTasks(n.id)}
              >
                <rect width={n.w} height={n.h} rx="6" fill="var(--bg-2)" stroke="currentColor" strokeWidth={n.mine ? 2 : 1} />
                <text x="8" y="17" fontSize="12" fill="var(--ink)">
                  {taskRef(n.id)} · {n.state}
                </text>
                <text x="8" y="33" fontSize="11" fill="var(--ink-2)">
                  {clip(n.reason ?? n.title, 26)}
                </text>
              </g>
            ))}
          </g>
        </svg>
      )}
    </section>
  );
}
