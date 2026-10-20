// The graph view of the Tasks page: one column per owner, boxes in bands, Done on top, Running in the middle, Queued below (the bands are
// app-core's `graphBands`); a thin line with an arrow joins the boxes of a column. A box slides to its new band when its task changes state; a click opens the task.
import { Fragment, useState } from 'react';
import { LayoutGroup, motion, useReducedMotion } from 'framer-motion';
import { graphBands, taskRef } from '@handline/app-core';
import type { Task } from '@handline/client';
import { goTasks } from './route.ts';
import { Button } from './ui/index.ts';

/** How many boxes a band shows before "+N more". */
const SHOWN = 7;
const BOX =
  'cursor-pointer truncate rounded-ctl border border-line bg-panel px-2.5 py-1.5 text-left text-body text-ink data-[band=done]:text-ink-3 data-[band=running]:border-working';
const COLUMN = 'min-w-[12rem] flex-1';

export function TaskBands({ tasks, now, recent }: { tasks: Task[]; now: number; recent: boolean }) {
  const reduced = useReducedMotion();
  const [zoom, setZoom] = useState(1);
  const [wide, setWide] = useState<ReadonlySet<string>>(new Set());
  const bands = graphBands(tasks, now, recent);
  const owners = [...new Set(bands.flatMap((b) => b.tasks.map((t) => t.to)))].sort();
  const nudge = (f: number) => setZoom((k) => Math.min(2, Math.max(0.5, k * f)));
  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Task graph">
      <div className="min-h-0 flex-1 overflow-auto" data-testid="task-graph">
        <div style={{ zoom }}>
          <LayoutGroup>
            <div className="flex gap-2 px-3 pt-3 pl-8">
              {owners.map((o) => (
                <h2 key={o} className={`${COLUMN} m-0 truncate text-label font-semibold text-ink-2`}>
                  {o}
                </h2>
              ))}
            </div>
            {bands.map((b) => {
              const open = wide.has(b.key);
              const shown = open ? b.tasks : b.tasks.slice(0, SHOWN * owners.length);
              return (
                <div key={b.key} className="flex gap-2 px-3 py-3 data-[band=running]:bg-raised" data-band={b.key}>
                  <h3 className="m-0 w-5 shrink-0 text-center text-hint font-semibold uppercase tracking-wide text-ink-3 [writing-mode:vertical-rl] rotate-180 data-[band=running]:text-working" data-band={b.key}>
                    {b.label} {b.tasks.length}
                    {b.key === 'done' && recent ? ' · last 24 h' : ''}
                  </h3>
                  {owners.map((o) => (
                    <div key={o} className={`${COLUMN} flex flex-col items-stretch gap-1`}>
                      {shown
                        .filter((t) => t.to === o)
                        .map((t, i) => {
                          const label = `${taskRef(t.id)} ${t.title}`;
                          const props = {
                            'data-node': t.id,
                            'data-state': t.state,
                            'data-band': b.key,
                            className: BOX,
                            title: `${label} · ${t.to}`,
                            onClick: () => goTasks(t.id),
                          };
                          return (
                            <Fragment key={t.id}>
                              {i > 0 ? <span className="text-center text-hint leading-none text-ink-3" aria-hidden="true">↓</span> : null}
                              {reduced ? (
                                <button type="button" {...props}>
                                  {label}
                                </button>
                              ) : (
                                <motion.button type="button" layout layoutId={`task-${t.id}`} {...props}>
                                  {label}
                                </motion.button>
                              )}
                            </Fragment>
                          );
                        })}
                    </div>
                  ))}
                  {b.tasks.length > shown.length ? (
                    <Button label={`Show ${b.tasks.length - shown.length} more`} onClick={() => setWide(new Set([...wide, b.key]))}>
                      +{b.tasks.length - shown.length} more
                    </Button>
                  ) : null}
                </div>
              );
            })}
          </LayoutGroup>
        </div>
      </div>
      <footer className="flex items-center gap-2 px-3 py-2 text-hint text-ink-3">
        <span className="mr-auto">A column is one agent; an arrow points to what runs next. A box slides to the next band when its task changes state.</span>
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
