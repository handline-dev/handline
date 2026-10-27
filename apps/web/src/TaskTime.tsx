// Where a task's time went, always one line above the body: a small bar and a sentence; a click opens one box with the full bar, the total per
// kind and each stretch with its reason, start and length, scrolling inside a short height. Read from task.time.
import { useState } from 'react';
import { clockTime, span, timeView, type TimeKind } from '@handline/app-core';
import type { TimeSegment } from '@handline/client';
import { useOff, useTaskTime } from '@handline/app-data';
import { Icon } from '#src/ui/index.ts';

const TONE: Record<TimeKind, string> = { working: 'bg-ok', waiting: 'bg-bad', queued: 'bg-line-strong', starting: 'bg-ink-3' };
const length = (seconds: number) => span(seconds * 1000);

const SAID: Record<TimeKind, string> = { working: 'worked', waiting: 'waited', queued: 'up next', starting: 'starting' };

/** The one-line Time: it draws nothing for a daemon without task.time or a task with no stretches yet. */
export function TimeLine({ id }: { id: number }) {
  const [open, setOpen] = useState(false);
  const off = useOff('task.time');
  const segments = useTaskTime(id).data?.segments ?? [];
  if (off || !segments.length) return null;
  const { rows, totals, seconds } = timeView(segments);
  const sentence = [...totals.map((t) => `${SAID[t.kind]} ${length(t.seconds)}`), `${length(seconds)} in all`].join(' · ');
  return (
    <section aria-label="Time" className="mt-2 rounded-card border border-line bg-surface px-2">
      <button
        type="button"
        aria-expanded={open}
        className="flex h-6 w-full min-w-0 cursor-pointer items-center gap-2 border-0 bg-transparent p-0 text-left text-hint text-ink-2"
        onClick={() => setOpen(!open)}
      >
        {open ? null : <MiniBar rows={rows} className="h-1.5 w-16 flex-none" />}
        <span className="min-w-0 flex-1 truncate">{sentence}</span>
        <Icon name="chevron" size={12} aria-hidden className={open ? 'rotate-180' : undefined} />
      </button>
      {open ? (
        <div className="max-h-48 overflow-auto pb-2">
          <TaskTime segments={segments} />
        </div>
      ) : null}
    </section>
  );
}

function MiniBar({ rows, className }: { rows: ReturnType<typeof timeView>['rows']; className: string }) {
  return (
    <span className={`flex overflow-hidden rounded-full bg-raised ${className}`} role="img" aria-label="Time by stretch">
      {rows.map((r, i) => (
        <span key={i} className={TONE[r.kind]} style={{ width: `${r.share * 100}%` }} title={`${r.text} · ${length(r.seconds)}`} />
      ))}
    </span>
  );
}

export function TaskTime({ segments }: { segments: TimeSegment[] }) {
  const { rows, totals, seconds } = timeView(segments);
  return (
    <div className="ttime" data-seconds={seconds}>
      <MiniBar rows={rows} className="mb-2 h-2 w-full" />
      <p className="m-0 mb-2 flex flex-wrap gap-x-4 gap-y-1" aria-label="Total per kind">
        {totals.map((t) => (
          <span key={t.kind} data-total={t.kind}>
            <span className={`mr-1 inline-block size-2 rounded-full ${TONE[t.kind]}`} aria-hidden="true" />
            {t.word} <strong>{length(t.seconds)}</strong>
          </span>
        ))}
        <span className="text-ink-3" data-total="all">
          in all {length(seconds)}
        </span>
      </p>
      {rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[5rem_minmax(0,1fr)_auto] gap-x-2 border-t border-line py-1.5" data-segment={r.kind}>
          <span className="mono whitespace-nowrap text-ink-3">{clockTime(r.from)}</span>
          <span>{r.text}</span>
          <span className="mono text-ink-2">{length(r.seconds)}</span>
        </div>
      ))}
    </div>
  );
}
