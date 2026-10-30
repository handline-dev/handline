// The usage outlook (stats.outlook), the head of the Usage page: "Week 43% left" and the week left of the accounts together, recorded to now and
// guessed after it, with each account's weekly reset under the axis and the part lost at the last one as a bar. The numbers are in the words
// and under the picture's aria-label, so the picture is never the only way to read them.
import { useState } from 'react';
import { dateTime, guessLine, lostAtLastReset, weekResets } from '@handline/app-core';
import type { Outlook } from '@handline/client';
import type { useOutlook } from '@handline/app-data';
import { GRID_LINE, LINE, NOW_LINE, RESET_LINE } from '#src/outlookStyle.ts';
import { LostBar, WeekReadout, XAxis, YMarks, type Labels } from '#src/OutlookWeek.tsx';
import { Cost } from '#src/OutlookCost.tsx';
import { Segmented, Select } from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

type Range = Outlook['range'];
type View = 'left' | 'cost';
const RANGES: Record<View, Range[]> = { left: ['12h', '24h', '3d', '7d', '14d'], cost: ['7d', '14d', '30d'] };
const W = 560;
const H = 160;
const LOOK_KEY = 'outlook-look';

export type Look = { view: View; range: Range };

/** The view and range last used are remembered by this window only; a browser that refuses storage starts on the week left over 7 days. */
export function useLook(): [Look, (l: Look) => void] {
  const [look, set] = useState<Look>(() => {
    try {
      const l = JSON.parse(localStorage.getItem(LOOK_KEY) ?? '{}');
      const view: View = l.view === 'cost' ? 'cost' : 'left';
      return { view, range: RANGES[view].includes(l.range) ? l.range : '7d' };
    } catch {
      return { view: 'left', range: '7d' };
    }
  });
  return [
    look,
    (l) => {
      set(l);
      try {
        localStorage.setItem(LOOK_KEY, JSON.stringify(l));
      } catch {
        /* not remembered */
      }
    },
  ];
}

/** The guess line ends at the last guessed reset: the daemon's line goes on as if the week did not reset, which is not a guess of anything. */
function clipAtReset(o: Outlook): Outlook {
  const end = lostAtLastReset(o);
  return end ? { ...o, future: o.future.filter((p) => Date.parse(p.at) <= Date.parse(end.at)) } : o;
}

function Line({ o: full, labels }: { o: Outlook; labels: Labels }) {
  const o = clipAtReset(full);
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
          <LostBar o={full} span={{ t0, t1 }} labels={labels} />
          <XAxis o={full} span={{ t0, t1 }} labels={labels} />
        </div>
      </div>
      <p className="outlook-readout muted m-0 mt-14 h-5 text-sm" aria-live="polite">
        {near ? `${dateTime(near.at)} · ${Math.round(near.left_pct!)}% left${near.guess ? ' (guess)' : ''}` : ''}
      </p>
    </>
  );
}

export function OutlookSection(props: { q: ReturnType<typeof useOutlook>; look: Look; setLook: (l: Look) => void; labels: Labels }) {
  const { q, look, setLook, labels } = props;
  const o = q.data;
  return (
    <section className="outlook mb-4 pt-4" aria-label={look.view === 'left' ? 'Week left' : 'Cost per day'}>
      <div className="flex items-baseline gap-3">
        <div className="flex-1">
          {o && look.view === 'left' ? (
            <WeekReadout o={o} labels={labels} />
          ) : look.view === 'cost' ? (
            <strong className="text-xl font-semibold">Cost per day</strong>
          ) : null}
        </div>
        <Segmented
          label="View"
          value={look.view}
          onChange={(view) => setLook({ view, range: RANGES[view].includes(look.range) ? look.range : '7d' })}
          options={[
            { value: 'left', label: 'Week left' },
            { value: 'cost', label: 'Cost per day' },
          ]}
        />
        <Select
          label="Range"
          value={look.range}
          onChange={(e) => setLook({ ...look, range: e.target.value as Range })}
          options={RANGES[look.view].map((r) => ({ value: r, label: r }))}
        />
      </div>
      <View q={q} served shape="stats" what="the outlook">
        {o ? look.view === 'left' ? <Line o={o} labels={labels} /> : <Cost key={look.range} o={o} /> : null}
      </View>
    </section>
  );
}
