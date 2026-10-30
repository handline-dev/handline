// The week-left view of the outlook around the line: the big "Week N% left" with what the pace loses, the axis marks (0/50/100, weekdays, each
// account's weekly reset), the lost part as a bar in blue, and the accounts one line each. Every number is the daemon's; none is made up here.
import { axisTicks, dateTime, lostResets, runwayWords, weekAccounts } from '@handline/app-core';
import type { Outlook } from '@handline/client';

const BAR = 'lost-bar absolute bottom-0 w-2.5 -translate-x-1/2 rounded-t-sm border border-acc';

type Span = { t0: number; t1: number };
const pos = ({ t0, t1 }: Span, at: string) => (t1 === t0 ? 0 : ((Date.parse(at) - t0) / (t1 - t0)) * 100);
const inSpan = (s: Span, at: string) => Date.parse(at) >= s.t0 && Date.parse(at) <= s.t1;

/** "Week 43% left" and the words for the pace: what is lost at the reset, or when it runs out. */
export function WeekHead({ o }: { o: Outlook }) {
  const left = o.past.filter((p) => p.left_pct !== null).at(-1)?.left_pct ?? null;
  const lost = weekAccounts(o).flatMap((a) => (a.lostPct === null ? [] : [a.lostPct]));
  const soonest = o.runway
    .flatMap((r) => (r.runs_out_in_seconds === null ? [] : [r]))
    .sort((a, b) => a.runs_out_in_seconds! - b.runs_out_in_seconds!)[0];
  const pace = lost.length
    ? `At this pace ${Math.round(lost.reduce((a, b) => a + b, 0) / lost.length)}% of the week is lost at the reset`
    : soonest
      ? `At this pace ${soonest.account} ${runwayWords(soonest)}`
      : '';
  return (
    <div className="week-head mb-2">
      <p className="week-left m-0 text-3xl font-semibold tabular-nums">
        {left === null ? 'Week: no reading' : `Week ${Math.round(left)}% left`}
      </p>
      {pace ? <p className="week-pace muted m-0 text-sm">{pace}</p> : null}
    </div>
  );
}

/** 0, 50 and 100 % beside the plot. */
export function YMarks() {
  return (
    <>
      {[0, 50, 100].map((g) => (
        <span
          key={g}
          className="y-mark muted absolute -left-10 w-8 -translate-y-1/2 text-right text-xs tabular-nums"
          style={{ top: `${100 - g}%` }}
        >
          {g}%
        </span>
      ))}
    </>
  );
}

/** Weekday (or clock, over a day and a half) labels under the plot, and a triangle on the axis at each account's weekly reset. */
export function XAxis({ o, span }: { o: Outlook; span: Span }) {
  const resets = (o.by_account ?? []).flatMap((a) =>
    a.resets.filter((r) => r.window === '7d' && inSpan(span, r.at)).map((r) => ({ account: a.account, at: r.at })),
  );
  return (
    <>
      <div className="x-axis absolute inset-x-0 top-full h-5" aria-hidden>
        {axisTicks(span.t0, span.t1).map((t) => (
          <span
            key={t.at}
            className="x-tick muted absolute -translate-x-1/2 text-xs"
            style={{ left: `${((t.at - span.t0) / (span.t1 - span.t0)) * 100}%` }}
          >
            {t.label}
          </span>
        ))}
      </div>
      {resets.map((r) => (
        <span
          key={`${r.account}${r.at}`}
          data-reset-mark={r.account}
          title={`${r.account}: the week resets ${dateTime(r.at)}`}
          className="reset-mark absolute -bottom-1 size-0 -translate-x-1/2 border-x-[5px] border-b-[8px] border-x-transparent border-b-wait"
          style={{ left: `${pos(span, r.at)}%` }}
        />
      ))}
    </>
  );
}

/** The part of the week left unused at a reset, a bar from the bottom: filled where it happened, outlined where it is the guess. */
export function LostBars({ o, span }: { o: Outlook; span: Span }) {
  const done = lostResets(o).filter((l) => inSpan(span, l.at));
  const guess = weekAccounts(o).flatMap((a) =>
    a.resetAt && a.lostPct !== null && inSpan(span, a.resetAt) ? [{ account: a.account, at: a.resetAt, pct: a.lostPct }] : [],
  );
  return (
    <>
      {done.map((l) => (
        <span
          key={`${l.account}${l.at}`}
          data-lost={l.account}
          title={`${l.account}: ${Math.round(l.unusedPct)}% of the week lost at the reset ${dateTime(l.at)}`}
          className={`${BAR} bg-acc`}
          style={{ left: `${pos(span, l.at)}%`, height: `${l.unusedPct}%` }}
        />
      ))}
      {guess.map((l) => (
        <span
          key={`g${l.account}${l.at}`}
          data-lost-guess={l.account}
          title={`${l.account}: ${Math.round(l.pct)}% of the week lost at the reset ${dateTime(l.at)} at this pace`}
          className={`${BAR} bg-transparent border-dashed`}
          style={{ left: `${pos(span, l.at)}%`, height: `${l.pct}%` }}
        />
      ))}
    </>
  );
}

/** Each account on one line: its week left, its next reset and what the pace loses at it. */
export function AccountLines({ o }: { o: Outlook }) {
  const rows = weekAccounts(o);
  if (!rows.length) return null;
  return (
    <ul className="week-accounts m-0 mt-3 flex list-none flex-col gap-1 p-0 text-sm" aria-label="Accounts">
      {rows.map((a) => (
        <li key={a.account} data-account={a.account} className="flex flex-wrap gap-x-4">
          <b className="min-w-24">{a.account}</b>
          <span className="tabular-nums">{a.leftPct === null ? 'no reading' : `${Math.round(a.leftPct)}% left`}</span>
          <span className="muted">{a.resetAt ? `resets ${dateTime(a.resetAt)}` : 'no reset in view'}</span>
          {a.lostPct !== null ? <span className="muted tabular-nums">{Math.round(a.lostPct)}% lost at this pace</span> : null}
        </li>
      ))}
    </ul>
  );
}
