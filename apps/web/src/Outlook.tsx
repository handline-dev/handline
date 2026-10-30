// The usage outlook (stats.outlook), the head of the Usage page: "Week 43% left" and the week left of the accounts together, recorded to now and
// guessed after it, with each account's weekly reset under the axis and the part lost at the last one as a bar. The numbers are in the words
// and under the picture's aria-label, so the picture is never the only way to read them.
import { useState } from 'react';
import { dateTime, guessLine, weekResets } from '@handline/app-core';
import type { Outlook } from '@handline/client';
import type { useOutlook } from '@handline/app-data';
import { GRID_LINE, LINE, NOW_LINE, RESET_LINE } from '#src/outlookStyle.ts';
import { LostBar, WeekReadout, XAxis, YMarks } from '#src/OutlookWeek.tsx';
import { Select } from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

type Range = Outlook['range'];
const RANGES: Range[] = ['12h', '24h', '3d', '7d', '14d'];
const W = 560;
const H = 160;
const RANGE_KEY = 'outlook-look';

/** The range last used is remembered by this window only; a browser that refuses storage starts on 7 days. */
export function useRange(): [Range, (r: Range) => void] {
  const [range, set] = useState<Range>(() => {
    try {
      const r = JSON.parse(localStorage.getItem(RANGE_KEY) ?? '{}').range;
      return RANGES.includes(r) ? r : '7d';
    } catch {
      return '7d';
    }
  });
  return [
    range,
    (r) => {
      set(r);
      try {
        localStorage.setItem(RANGE_KEY, JSON.stringify({ range: r }));
      } catch {
        /* not remembered */
      }
    },
  ];
}

function Line({ o }: { o: Outlook }) {
  const all = [...o.past, ...o.future].filter((p) => p.left_pct !== null);
  const t0 = Date.parse((all[0] ?? { at: o.now }).at);
  const t1 = Date.parse((all.at(-1) ?? { at: o.now }).at);
  const x = (at: string) => (t1 === t0 ? 0 : ((Date.parse(at) - t0) / (t1 - t0)) * W);
  const y = (pct: number) => H - (pct / 100) * H;
  const [cursor, setCursor] = useState<number | null>(null);
  const points = [...o.past.map((p) => ({ ...p, guess: false })), ...o.future.map((p) => ({ ...p, guess: true }))].filter(
    (p) => p.left_pct !== null,
  );
  const near =
    cursor === null
      ? null
      : points.reduce((best, p) => (Math.abs(x(p.at) - cursor) < Math.abs(x(best.at) - cursor) ? p : best), points[0]!);
  const resets = weekResets(o).filter((r) => Date.parse(r.at) >= t0 && Date.parse(r.at) <= t1);
  const scrub = (e: React.PointerEvent<HTMLDivElement>) => {
    const svg = e.currentTarget.querySelector('svg')!.getBoundingClientRect();
    setCursor(Math.min(W, Math.max(0, ((e.clientX - svg.left) / (svg.width || 1)) * W)));
  };
  const path = (pts: Outlook['past']) =>
    pts
      .filter((p) => p.left_pct !== null)
      .map((p, i) => `${i ? 'L' : 'M'}${x(p.at).toFixed(1)},${y(p.left_pct!).toFixed(1)}`)
      .join(' ');
  return (
    <>
      <div
        className="outlook-plot relative mt-4 touch-pan-y pl-11"
        onPointerMove={scrub}
        onPointerDown={scrub}
        onPointerLeave={() => setCursor(null)}
      >
        <div className="outlook-frame relative">
          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            className="outlook-svg h-[240px] w-full overflow-visible"
            role="img"
            aria-label={`Week left, the ${o.accounts} accounts with a reading together, recorded and guessed`}
          >
            {[0, 50, 100].map((g) => (
              <line key={g} x1={0} x2={W} y1={y(g)} y2={y(g)} className={GRID_LINE} />
            ))}
            {resets.map((r) => (
              <line key={`${r.account}${r.at}`} data-reset={r.at} x1={x(r.at)} x2={x(r.at)} y1={0} y2={H} className={RESET_LINE} />
            ))}
            <path d={path(o.past)} className={`${LINE} past`} fill="none" />
            <path d={path(guessLine(o))} className={`${LINE} future`} fill="none" strokeDasharray="5 4" />
            <line x1={x(o.now)} x2={x(o.now)} y1={0} y2={H} className={NOW_LINE} />
            {near ? (
              <line x1={x(near.at)} x2={x(near.at)} y1={0} y2={H} className="cursor stroke-ink-3 [vector-effect:non-scaling-stroke]" />
            ) : null}
          </svg>
          <span
            className="now-label absolute -top-5 -translate-x-1/2 text-sm"
            style={{ left: `${((Date.parse(o.now) - t0) / (t1 - t0 || 1)) * 100}%` }}
          >
            now
          </span>
          <YMarks />
          <LostBar o={o} span={{ t0, t1 }} />
          <XAxis o={o} span={{ t0, t1 }} />
        </div>
      </div>
      <p className="outlook-readout muted m-0 mt-14 h-5 text-sm" aria-live="polite">
        {near ? `${dateTime(near.at)} · ${Math.round(near.left_pct!)}% left${near.guess ? ' (guess)' : ''}` : ''}
      </p>
    </>
  );
}

export function OutlookSection({ q, range, setRange }: { q: ReturnType<typeof useOutlook>; range: Range; setRange: (r: Range) => void }) {
  const o = q.data;
  return (
    <section className="outlook mb-4" aria-label="Week left">
      <View q={q} served shape="stats" what="the outlook">
        {o ? (
          <>
            <div className="flex items-baseline gap-3">
              <div className="flex-1">
                <WeekReadout o={o} />
              </div>
              <Select
                label="Range"
                value={range}
                onChange={(e) => setRange(e.target.value as Range)}
                options={RANGES.map((r) => ({ value: r, label: r }))}
              />
            </div>
            <Line o={o} />
          </>
        ) : null}
      </View>
    </section>
  );
}
