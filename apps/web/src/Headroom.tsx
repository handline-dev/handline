// The Headroom tab of the Stats section (stats.headroom): the share of the usage windows left, a few days back as a solid line and as many ahead
// as a dashed guess, now in the middle. One line for all accounts, or one per account; resets as ticks on the floor (the weekly ones taller);
// a drag, or the arrow keys, puts a vertical line and a balloon with the percent at that time.
import { useMemo, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { dateTime, headroomPoints, headroomStamp, headroomSummary, nearestPoint } from '@handline/app-core';
import type { Headroom } from '@handline/client';
import { useHeadroom } from '@handline/app-data';
import { GRID_LINE, LEGEND, LEGEND_ITEM, LINE, NOW_LINE } from '#src/outlookStyle.ts';
import { Button, ChartCard, Segmented } from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

const SPANS = ['1', '3', '7'] as const;
type Days = (typeof SPANS)[number];
const DAY_MS = 86_400_000;
const W = 720;
const H = 280;
const LOOK = ['stroke-ink', 'stroke-ink-2', 'stroke-ink-3', 'stroke-line-strong'];

type Pt = { at: string; pct: number | null };
const effective = (p: { at: string; five_left: number | null; week_left: number | null }): Pt => ({
  at: p.at,
  pct: p.five_left === null || p.week_left === null ? (p.five_left ?? p.week_left) : Math.min(p.five_left, p.week_left),
});

function Chart({ h, perAccount, days }: { h: Headroom; perAccount: boolean; days: number }) {
  const all = headroomPoints(h);
  const t0 = Date.parse(all[0]?.at ?? h.now);
  const t1 = Date.parse(all.at(-1)?.at ?? h.now);
  const now = Date.parse(h.now);
  const [cursor, setCursor] = useState<number | null>(null);
  const x = (t: number) => (t1 === t0 ? 0 : ((t - t0) / (t1 - t0)) * W);
  const y = (pct: number) => H - (Math.min(100, Math.max(0, pct)) / 100) * H;
  const path = (pts: Pt[]) =>
    pts
      .filter((p) => p.pct !== null)
      .map((p, i) => `${i ? 'L' : 'M'}${x(Date.parse(p.at)).toFixed(1)},${y(p.pct!).toFixed(1)}`)
      .join(' ');
  const lines = useMemo(
    () =>
      perAccount
        ? h.accounts.map((a) => ({ name: a.account, past: a.past.map(effective), future: a.future.map(effective) }))
        : [
            {
              name: 'all accounts',
              past: h.combined.past.map((p) => ({ at: p.at, pct: p.left_pct })),
              future: h.combined.future.map((p) => ({ at: p.at, pct: p.left_pct })),
            },
          ],
    [h, perAccount],
  );
  const resets = h.accounts
    .flatMap((a) => a.resets.map((r) => ({ ...r, account: a.account })))
    .filter((r, i, rs) => rs.findIndex((o) => o.at === r.at && o.window === r.window && o.account === r.account) === i);
  // The weekly share across accounts: the floor under the 5h swings, and the line that says how much work is left.
  const floor = useMemo(() => {
    const pick = (side: 'past' | 'future'): Pt[] =>
      (h.accounts[0]?.[side] ?? []).map((p, i) => {
        const v = h.accounts.map((a) => a[side][i]?.week_left).filter((n): n is number => typeof n === 'number');
        return { at: p.at, pct: v.length ? v.reduce((a, b) => a + b, 0) / v.length : null };
      });
    return { past: pick('past'), future: pick('future') };
  }, [h]);
  const ticks = useMemo(() => {
    const out: { t: number; label: string }[] = [];
    const every = days === 1 ? 6 : 24;
    const d = new Date(t0);
    d.setHours(Math.ceil(d.getHours() / every) * every, 0, 0, 0);
    for (; d.getTime() <= t1; d.setHours(d.getHours() + every))
      out.push({
        t: d.getTime(),
        label: d.toLocaleString(
          [],
          days === 1 ? { weekday: 'short', hour: '2-digit', minute: '2-digit' } : { weekday: 'short', day: 'numeric' },
        ),
      });
    return out;
  }, [t0, t1, days]);
  const at = (e: PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    setCursor(t0 + Math.min(1, Math.max(0, (e.clientX - box.left) / box.width)) * (t1 - t0));
  };
  const step = (e: KeyboardEvent<SVGSVGElement>) => {
    const d = e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : 0;
    if (!d) return;
    e.preventDefault();
    setCursor(Math.min(t1, Math.max(t0, (cursor ?? now) + d * h.step_s * 1000)));
  };
  const reads =
    cursor === null ? [] : lines.map((l) => ({ name: l.name, p: nearestPoint([...l.past, ...l.future], cursor, (p) => p.pct !== null) }));
  const shown = reads.find((r) => r.p)?.p ?? null;
  const floorRead = cursor === null || perAccount ? null : nearestPoint([...floor.past, ...floor.future], cursor, (p) => p.pct !== null);
  const cx = cursor === null ? 0 : x(cursor);
  return (
    <div className="headroom-plot relative">
      <div className="relative pl-9">
        <span className="headroom-yaxis pointer-events-none absolute inset-y-0 left-0 w-8 text-hint text-ink-3" aria-hidden>
          {[100, 50, 0].map((g) => (
            <span key={g} className="absolute right-0 -translate-y-1/2 tabular-nums" style={{ top: `${(y(g) / H) * 100}%` }}>
              {g}%
            </span>
          ))}
        </span>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className="headroom-svg h-[280px] w-full cursor-col-resize touch-none outline-offset-2"
          role="img"
          tabIndex={0}
          aria-label={`Share of the usage windows left, ${h.accounts.length === 1 ? 'one account' : `${h.accounts.length} accounts`}, recorded and guessed. Left and right arrows move a marker.`}
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            at(e);
          }}
          onPointerMove={at}
          onPointerLeave={() => setCursor(null)}
          onKeyDown={step}
          onBlur={() => setCursor(null)}
        >
          {[0, 50, 100].map((g) => (
            <line key={g} x1={0} x2={W} y1={y(g)} y2={y(g)} className={GRID_LINE} />
          ))}
          {resets.map((r) => (
            <line
              key={`${r.at}${r.window}${r.account}`}
              x1={x(Date.parse(r.at))}
              x2={x(Date.parse(r.at))}
              y1={H}
              y2={r.window === '7d' ? H - 18 : H - 7}
              className={`reset ${r.window} ${r.window === '7d' ? 'stroke-ink stroke-2' : 'stroke-line-strong'} [vector-effect:non-scaling-stroke]`}
              data-reset={r.window}
            />
          ))}
          {lines.map((l, i) => (
            <g key={l.name} data-line={l.name} className={perAccount ? '' : 'opacity-40'}>
              <path d={path(l.past)} className={`past ${LOOK[i % LOOK.length]} stroke-2 [vector-effect:non-scaling-stroke]`} fill="none" />
              <path
                d={path(l.future)}
                className={`future ${LOOK[i % LOOK.length]} stroke-2 [vector-effect:non-scaling-stroke]`}
                fill="none"
                strokeDasharray="5 4"
              />
            </g>
          ))}
          {perAccount ? null : (
            <g data-floor>
              <path d={path(floor.past)} className="past stroke-ink stroke-[3] [vector-effect:non-scaling-stroke]" fill="none" />
              <path
                d={path(floor.future)}
                className="future stroke-ink stroke-[3] [vector-effect:non-scaling-stroke]"
                fill="none"
                strokeDasharray="6 4"
              />
            </g>
          )}
          <line x1={x(now)} x2={x(now)} y1={0} y2={H} className={NOW_LINE} />
          {cursor === null ? null : <line x1={cx} x2={cx} y1={0} y2={H} className="cursor stroke-ink [vector-effect:non-scaling-stroke]" />}
        </svg>
        <span
          className="pointer-events-none absolute top-0 -translate-x-1/2 text-hint text-ink-3"
          style={{ left: `calc(2.25rem + (100% - 2.25rem) * ${x(now) / W})` }}
        >
          now
        </span>
        {resets
          .filter((r) => r.window === '7d' || (r.at === h.next_reset?.at && r.account === h.next_reset.account))
          .map((r) => (
            <span
              key={`${r.at}${r.window}${r.account}`}
              className="headroom-reset-name pointer-events-none absolute bottom-5 -translate-x-1/2 text-hint text-ink-3"
              style={{ left: `calc(2.25rem + (100% - 2.25rem) * ${x(Date.parse(r.at)) / W})` }}
            >
              {r.account} {r.window === '7d' ? 'week' : '5 h'}
            </span>
          ))}
        {cursor !== null && shown ? (
          <div
            className="headroom-balloon pointer-events-none absolute top-0 -translate-x-1/2 rounded-ctl bg-raised px-2 py-1 text-label whitespace-nowrap shadow-1"
            style={{ left: `clamp(70px, ${(cx / W) * 100}%, calc(100% - 70px))` }}
            role="status"
          >
            {perAccount ? (
              <>
                <div className="muted">{dateTime(shown.at)}</div>
                {reads.map((r) => (
                  <div key={r.name}>
                    {r.name} {r.p?.pct === null || !r.p ? 'no data' : `${Math.round(r.p.pct!)}%`}
                  </div>
                ))}
              </>
            ) : (
              <>
                <div>{headroomStamp(shown.at, shown.pct)}</div>
                {floorRead ? <div className="muted">week floor {Math.round(floorRead.pct!)}%</div> : null}
              </>
            )}
          </div>
        ) : null}
      </div>
      <div className="headroom-xaxis relative mt-1 ml-9 h-4 text-hint text-ink-3" aria-hidden>
        {ticks.map((k) => (
          <span key={k.t} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: `${(x(k.t) / W) * 100}%` }}>
            {k.label}
          </span>
        ))}
      </div>
      <ul className={LEGEND} aria-label="Lines">
        <li className={LEGEND_ITEM}>
          <svg viewBox="0 0 24 8" width="24" height="8" aria-hidden>
            <line x1="0" x2="24" y1="4" y2="4" className={`${LINE} past`} />
          </svg>{' '}
          recorded
        </li>
        <li className={LEGEND_ITEM}>
          <svg viewBox="0 0 24 8" width="24" height="8" aria-hidden>
            <line x1="0" x2="24" y1="4" y2="4" className={`${LINE} future`} strokeDasharray="5 4" />
          </svg>{' '}
          guess
        </li>
        {perAccount ? null : (
          <li className={LEGEND_ITEM}>
            <svg viewBox="0 0 24 8" width="24" height="8" aria-hidden>
              <line x1="0" x2="24" y1="4" y2="4" className="stroke-ink stroke-[3]" />
            </svg>{' '}
            week left (bold), 5 h swings (faint)
          </li>
        )}
        <li className={LEGEND_ITEM}>
          <svg viewBox="0 0 8 16" width="8" height="16" aria-hidden>
            <line x1="4" x2="4" y1="0" y2="16" className="stroke-ink stroke-2" />
          </svg>{' '}
          weekly reset
        </li>
        <li className={LEGEND_ITEM}>
          <svg viewBox="0 0 8 16" width="8" height="16" aria-hidden>
            <line x1="4" x2="4" y1="9" y2="16" className="stroke-line-strong stroke-2" />
          </svg>{' '}
          5 h reset
        </li>
        {perAccount
          ? lines.map((l, i) => (
              <li key={l.name} className={LEGEND_ITEM}>
                <svg viewBox="0 0 24 8" width="24" height="8" aria-hidden>
                  <line x1="0" x2="24" y1="4" y2="4" className={`${LOOK[i % LOOK.length]} stroke-2`} />
                </svg>{' '}
                {l.name}
              </li>
            ))
          : null}
      </ul>
    </div>
  );
}

/** Seconds between points so a span gets about 480: a minute at least, a whole number of minutes. */
const stepFor = (days: number) => Math.max(60, Math.ceil((days * 2 * 86_400) / 480 / 60) * 60);

export function HeadroomTab() {
  const [days, setDays] = useState<Days>('3');
  const [perAccount, setPerAccount] = useState(false);
  // The span is fixed per choice: a clock read on every render would make a new query each time.
  const range = useMemo(() => {
    const now = Math.floor(Date.now() / 60_000) * 60_000;
    return { from: new Date(now - +days * DAY_MS).toISOString(), to: new Date(now + +days * DAY_MS).toISOString() };
  }, [days]);
  const q = useHeadroom(range.from, range.to, stepFor(+days));
  const h = q.data;
  return (
    <ChartCard className="headroom mt-3" aria-label="Headroom">
      <div className="headroom-controls mb-2 flex flex-wrap items-center gap-2">
        <h3 className="m-0 flex-1 text-title font-semibold">Headroom</h3>
        <Segmented
          label="Span"
          value={days}
          onChange={setDays}
          options={SPANS.map((d) => ({ value: d, label: d === '1' ? '1 day' : `${d} days` }))}
        />
        <Button label="Per account" variant="outline" aria-pressed={perAccount} onClick={() => setPerAccount(!perAccount)} />
      </div>
      <View q={q} served shape="stats" what="the headroom">
        {h ? (
          <>
            <p className="headroom-summary m-0 mb-2 text-body font-semibold" data-summary>
              {headroomSummary(h)}
            </p>
            <Chart h={h} perAccount={perAccount} days={+days} />
          </>
        ) : null}
      </View>
    </ChartCard>
  );
}
