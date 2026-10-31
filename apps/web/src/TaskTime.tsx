// Where a task's time went, always one line above the body: a small bar and a sentence; a click opens one box with the full bar, the total per
// kind and each stretch with its reason, start and length, scrolling inside a short height. Read from task.time when the task opens, for the sentence.
import { CircleAlert } from 'lucide-react';
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

/** What each kind of stretch is called in the sentence under the bar. */
const SAY: Record<TimeLabel, string> = {
  working: 'worked',
  founder: 'waiting for you',
  task: 'blocked',
  agent: 'waiting on an agent',
  account: 'at a usage limit',
  waiting: 'waiting',
  queued: 'queued',
  starting: 'starting',
};

/**
 * Where the time went: a thin bar, and under it the same facts in words, "2h 42m in all · waiting for you 2h 31m · worked 10m"; an open task
 * leads with what is left, "39m left of 1h". The grey at the bar's end is the time still left. A click opens the stretches. It draws nothing for a
 * daemon without task.time or a task with no stretches yet.
 */
export function TimeLine({ id, left, rest, blocked }: { id: number; left?: string; rest?: number; blocked?: boolean }) {
  const [open, setOpen] = useState(false);
  const off = useOff('task.time');
  const segments = useTaskTime(id).data?.segments ?? [];
  // Amber, not the blocked state's red: the task waits on another task, nothing has failed.
  const chip = blocked ? (
    <Chip kind="warn" icon={CircleAlert}>
      Blocked
    </Chip>
  ) : null;
  if (off || !segments.length)
    return chip ? (
      <div className="mt-3 flex items-center gap-2 text-sm text-ink-2">
        {left ? <span className="tabular-nums">{left}</span> : null}
        {chip}
      </div>
    ) : null;
  const { rows, totals, seconds } = timeView(segments);
  const spare = rest && rest > 0 ? rest : 0;
  const whole = seconds + spare;
  const sentence = [...totals.map((t) => `${SAY[t.label]} ${length(t.seconds)}`), `${length(seconds)} in all`].join(' · ');
  return (
    <section aria-label="Time" className="mt-3">
      <button
        type="button"
        aria-expanded={open}
        aria-label={sentence}
        className="grid w-full min-w-0 cursor-pointer gap-1.5 border-0 bg-transparent p-0 text-left text-sm text-ink-2"
        onClick={() => setOpen(!open)}
      >
        <MiniBar rows={rows} whole={whole} className="h-1 w-full" />
        <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5">
          {spare && left ? (
            <span>
              <strong className="font-medium text-ink">{left}</strong> of {length(whole)}
            </span>
          ) : (
            <span>
              <strong className="font-medium text-ink">{length(seconds)}</strong> in all
            </span>
          )}
          {totals.map((t) => (
            <span key={t.label} className="inline-flex items-center gap-1">
              <span className={`inline-block size-1.5 rounded-full ${TONE[t.label]}`} aria-hidden="true" />
              {SAY[t.label]} {length(t.seconds)}
            </span>
          ))}
          {chip}
        </span>
      </button>
      {open ? (
        <div className="mt-1 max-h-48 overflow-auto rounded-md border border-line bg-surface px-2 py-2">
          <TaskTime segments={segments} />
        </div>
      ) : null}
    </section>
  );
}

function MiniBar({ rows, whole, className }: { rows: ReturnType<typeof timeView>['rows']; whole?: number; className: string }) {
  const total = rows.reduce((n, r) => n + r.seconds, 0);
  const scale = whole && total ? total / whole : 1;
  return (
    <span className={`flex gap-px overflow-hidden rounded-full bg-raised ${className}`} role="img" aria-label="Time by stretch">
      {rows.map((r, i) => (
        <span key={i} className={TONE[r.label]} style={{ width: `${r.share * scale * 100}%` }} title={`${r.text} · ${length(r.seconds)}`} />
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
