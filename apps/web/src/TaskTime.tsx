// Where a task's time went, always one line above the body: a small bar and a sentence; a click opens one box with the full bar, the total per
// kind and each stretch with its reason, start and length, scrolling inside a short height. Read from task.time when the task opens, for the sentence.
import { useState } from 'react';
import { clockTime, span, timeView, type TimeLabel } from '@handline/app-core';
import type { TimeSegment } from '@handline/client';
import { useOff, useTaskTime } from '@handline/app-data';
import { Chip } from '#src/ds/Chip.tsx';
import { Icon } from '#src/ui/index.ts';

const TONE: Record<TimeLabel, string> = {
  working: 'bg-ok',
  founder: 'bg-wait',
  task: 'bg-bad',
  agent: 'bg-ink-2',
  account: 'bg-bad',
  waiting: 'bg-ink-2',
  queued: 'bg-line-strong',
  starting: 'bg-ink-3',
};
/** 4m, 58m, 1h 40m: the short form the rest of the app writes. */
const length = (seconds: number) => span(seconds * 1000).replace(/ ([dhm])\b/g, '$1');

const SAID: Record<TimeLabel, string> = {
  working: 'worked',
  founder: 'needs you for',
  task: 'blocked for',
  agent: 'waiting on an agent for',
  account: 'at a usage limit for',
  waiting: 'waiting for',
  queued: 'waiting to start for',
  starting: 'starting for',
};

/** The one-line Time: "40m left", a short bar and the word for the stretch it is in; a click opens the full sentence, bar and stretches. It draws nothing for a daemon without task.time or a task with no stretches yet. */
export function TimeLine({ id, left, blocked }: { id: number; left?: string; blocked?: boolean }) {
  const [open, setOpen] = useState(false);
  const off = useOff('task.time');
  const segments = useTaskTime(id).data?.segments ?? [];
  const chip = blocked ? <Chip>Blocked</Chip> : null;
  if (off || !segments.length)
    return chip ? (
      <div className="mt-3 flex items-center gap-2 text-sm text-ink-2">
        {left ? <span className="tabular-nums">{left}</span> : null}
        {chip}
      </div>
    ) : null;
  const { rows, totals, seconds } = timeView(segments);
  const said = [...totals.map((t) => `${SAID[t.label]} ${length(t.seconds)}`), `${length(seconds)} in all`].join(' · ');
  const sentence = said[0]!.toUpperCase() + said.slice(1);
  const now = rows.at(-1);
  const word = totals.find((t) => t.label === now?.label)?.word;
  return (
    <section aria-label="Time" className="mt-3">
      <button
        type="button"
        aria-expanded={open}
        aria-label={sentence}
        className="flex h-6 min-w-0 cursor-pointer items-center gap-2 border-0 bg-transparent p-0 text-left text-sm text-ink-2"
        onClick={() => setOpen(!open)}
      >
        {left ? <span className="tabular-nums">{left}</span> : null}
        <MiniBar rows={rows} className="h-1.5 w-20 flex-none" />
        <span className="min-w-0 truncate">{word ?? length(seconds)}</span>
        {chip}
        <Icon name="chevron" size={12} aria-hidden className={open ? 'rotate-180' : undefined} />
      </button>
      {open ? (
        <div className="mt-1 max-h-48 overflow-auto rounded-md border border-line bg-surface px-2 py-2">
          <p className="m-0 mb-2 text-xs text-ink-2">{sentence}</p>
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
        <span key={i} className={TONE[r.label]} style={{ width: `${r.share * 100}%` }} title={`${r.text} · ${length(r.seconds)}`} />
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
          <span key={t.label} data-total={t.label}>
            <span className={`mr-1 inline-block size-2 rounded-full ${TONE[t.label]}`} aria-hidden="true" />
            {t.word} <strong>{length(t.seconds)}</strong>
          </span>
        ))}
        <span className="text-ink-3" data-total="all">
          in all {length(seconds)}
        </span>
      </p>
      {rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[5rem_minmax(0,1fr)_auto] gap-x-2 border-t border-line py-1.5" data-segment={r.label}>
          <span className="mono whitespace-nowrap text-ink-3">{clockTime(r.from)}</span>
          <span>{r.text}</span>
          <span className="mono text-ink-2">{length(r.seconds)}</span>
        </div>
      ))}
    </div>
  );
}
