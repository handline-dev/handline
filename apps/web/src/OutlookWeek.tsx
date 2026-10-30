// The week-left view's parts around the line: the readout above it, the 0/50/100% marks, the weekday and reset labels under it, and the one lost bar.
// Every number is the daemon's; none is made up here.
import { axisTicks, dateTime, lostAtLastReset, runwayWords, weekResets } from '@handline/app-core';
import type { Outlook } from '@handline/client';

/** The label of an account by its name, as the account rows show it. */
export type Labels = Record<string, string>;
const named = (l: Labels, a: string) => l[a] ?? a;

export type Span = { t0: number; t1: number };
const pos = ({ t0, t1 }: Span, at: string) => (t1 === t0 ? 0 : ((Date.parse(at) - t0) / (t1 - t0)) * 100);
/** "Fri 09:00". */
const resetAt = (at: string) => new Date(at).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const inSpan = (s: Span, at: string) => Date.parse(at) >= s.t0 && Date.parse(at) <= s.t1;

/** "Week 43% left" and, after it at body size, what the pace loses at the last reset (or when an account runs out). */
export function WeekReadout({ o, labels }: { o: Outlook; labels: Labels }) {
  const left = o.past.filter((p) => p.left_pct !== null).at(-1)?.left_pct ?? null;
  const lost = lostAtLastReset(o);
  const soonest = o.runway.filter((r) => r.runs_out_in_seconds !== null).sort((a, b) => a.runs_out_in_seconds! - b.runs_out_in_seconds!)[0];
  const pace = lost
    ? `at this pace about ${Math.round(lost.pct)}% of ${named(labels, lost.account)}'s week goes unused`
    : soonest
      ? `at this pace ${soonest.account} ${runwayWords(soonest)}`
      : '';
  return (
    <p className="week-readout m-0 flex flex-wrap items-baseline gap-x-2">
      <strong className="week-left text-xl font-semibold tabular-nums">
        {left === null ? 'Week: no reading' : `Week ${Math.round(left)}% left`}
      </strong>
      {pace ? <span className="week-pace">· {pace}</span> : null}
    </p>
  );
}

/** 0, 50 and 100 % at the left of the plot. */
export function YMarks() {
  return (
    <>
      {[0, 50, 100].map((g) => (
        <span
          key={g}
          className="y-mark muted absolute -left-11 w-9 -translate-y-1/2 text-right text-sm tabular-nums"
          style={{ top: `${100 - g}%` }}
        >
          {g}%
        </span>
      ))}
    </>
  );
}

/** Under the plot: the weekday (clock over a day and a half) labels, and under them each account's weekly reset with its time. */
export function XAxis({ o, span, labels }: { o: Outlook; span: Span; labels: Labels }) {
  const resets = weekResets(o).filter((r) => inSpan(span, r.at));
  return (
    <div className="x-axis absolute inset-x-0 top-full pt-2 text-sm" aria-hidden>
      <div className="relative h-5">
        {axisTicks(span.t0, span.t1).map((t) => (
          <span
            key={t.at}
            className="x-tick muted absolute -translate-x-1/2"
            style={{ left: `${((t.at - span.t0) / (span.t1 - span.t0)) * 100}%` }}
          >
            {t.label}
          </span>
        ))}
      </div>
      <div className="relative h-5">
        {resets.map((r) => (
          <span
            key={`${r.account}${r.at}`}
            data-reset-mark={r.account}
            className="reset-mark muted absolute whitespace-nowrap"
            style={{ left: `${pos(span, r.at)}%`, transform: pos(span, r.at) > 85 ? 'translateX(-100%)' : 'translateX(-50%)' }}
          >
            {named(labels, r.account)} {resetAt(r.at)}
          </span>
        ))}
      </div>
    </div>
  );
}

/** The one lost bar: the week left unused at the last guessed reset, from 0% up, with its words beside it. */
export function LostBar({ o, span, labels }: { o: Outlook; span: Span; labels: Labels }) {
  const lost = lostAtLastReset(o);
  if (!lost || !inSpan(span, lost.at)) return null;
  const x = pos(span, lost.at);
  return (
    <>
      <span
        data-lost
        className="lost-bar absolute bottom-px w-3 -translate-x-full bg-acc"
        style={{ left: `${x}%`, height: `${lost.pct}%` }}
        aria-hidden
      />
      <a
        href="#/requests"
        className="lost-link absolute -translate-y-full pr-4 text-right whitespace-nowrap text-acc-ink no-underline"
        style={{ right: `${100 - x}%`, bottom: `${lost.pct}%` }}
        title={`${named(labels, lost.account)}: ${Math.round(lost.pct)}% of the week is lost at the reset ${dateTime(lost.at)} at this pace`}
      >
        {Math.round(lost.pct)}% lost ›
      </a>
    </>
  );
}
