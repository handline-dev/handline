// The graph view of the Tasks page: boxes in bands, Done on top, Running in the middle, Queued below, "unblocks" between them (the layout is
// app-core's `graphBands`). A box slides to its new band when its task changes state; a click opens the task.
import { Fragment, useState } from 'react';
import { LayoutGroup, motion, useReducedMotion } from 'framer-motion';
import { graphBands, taskRef } from '@handline/app-core';
import type { Task } from '@handline/client';
import { goTasks } from './route.ts';
import { Button } from './ui/index.ts';

/** How many boxes a band shows before "+N more". */
const SHOWN = 7;
const BOX =
  'cursor-pointer truncate rounded-ctl border border-line bg-panel px-2.5 py-1.5 text-left text-body text-ink max-w-[22rem] data-[band=done]:text-ink-3 data-[band=running]:border-working';

export function TaskBands({ tasks, now, recent }: { tasks: Task[]; now: number; recent: boolean }) {
  const reduced = useReducedMotion();
  const [zoom, setZoom] = useState(1);
  const [wide, setWide] = useState<ReadonlySet<string>>(new Set());
  const bands = graphBands(tasks, now, recent);
  const nudge = (f: number) => setZoom((k) => Math.min(2, Math.max(0.5, k * f)));
  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Task graph">
      <div className="min-h-0 flex-1 overflow-auto" data-testid="task-graph">
        <div style={{ zoom }}>
          <LayoutGroup>
            {bands.map((b, i) => {
              const open = wide.has(b.key);
              const list = open ? b.tasks : b.tasks.slice(0, SHOWN);
              return (
                <Fragment key={b.key}>
                  <div className="px-3 py-3 data-[band=running]:bg-raised" data-band={b.key}>
                    <h2 className="m-0 mb-2 text-label font-semibold data-[band=running]:text-working">
                      {b.label} <span className="font-normal text-ink-3">{b.tasks.length}</span>
                      {b.key === 'done' && recent ? <span className="font-normal text-ink-3"> · last 24 h</span> : null}
                    </h2>
                    <div className="flex flex-wrap gap-2">
                      {list.map((t) => {
                        const label = `${taskRef(t.id)} ${t.title}`;
                        const props = {
                          'data-node': t.id,
                          'data-state': t.state,
                          'data-band': b.key,
                          className: BOX,
                          title: `${label} · ${t.to}`,
                          onClick: () => goTasks(t.id),
                        };
                        return reduced ? (
                          <button key={t.id} type="button" {...props}>
                            {label}
                          </button>
                        ) : (
                          <motion.button key={t.id} type="button" layout layoutId={`task-${t.id}`} {...props}>
                            {label}
                          </motion.button>
                        );
                      })}
                      {b.tasks.length > list.length ? (
                        <Button label={`Show ${b.tasks.length - list.length} more`} onClick={() => setWide(new Set([...wide, b.key]))}>
                          +{b.tasks.length - list.length} more
                        </Button>
                      ) : null}
                    </div>
                  </div>
                  {i < bands.length - 1 ? <div className="py-1 text-center text-hint text-ink-3">↓ unblocks</div> : null}
                </Fragment>
              );
            })}
          </LayoutGroup>
        </div>
      </div>
      <footer className="flex items-center gap-2 px-3 py-2 text-hint text-ink-3">
        <span className="mr-auto">A box slides to the next band when its task changes state.</span>
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
