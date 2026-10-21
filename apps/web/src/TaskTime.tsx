// Where a task's time went: a bar of its segments sized by length, the total per kind, then each segment with its reason, start and length.
// Read from task.time once opened from More.
import { clockTime, span, timeView, type TimeKind } from '@handline/app-core';
import type { TimeSegment } from '@handline/client';

const TONE: Record<TimeKind, string> = { working: 'bg-ok', waiting: 'bg-bad', queued: 'bg-line-strong', starting: 'bg-ink-3' };
const length = (seconds: number) => span(seconds * 1000);

export function TaskTime({ segments }: { segments: TimeSegment[] }) {
  const { rows, totals, seconds } = timeView(segments);
  return (
    <div className="ttime" data-seconds={seconds}>
      <div className="mb-2 flex h-2 overflow-hidden rounded-full bg-raised" role="img" aria-label="Time by stretch">
        {rows.map((r, i) => (
          <span key={i} className={TONE[r.kind]} style={{ width: `${r.share * 100}%` }} title={`${r.text} · ${length(r.seconds)}`} />
        ))}
      </div>
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
