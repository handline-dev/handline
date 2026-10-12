// One usage window on one line: label, bar, number and, from 80%, a warning glyph (a full window has its own glyph). The fill is in the tone
// of its percentage and a tick shows how far through the window the clock is, but the number and the glyph carry the state, so colour is never
// the only signal. The reset time is the tooltip; the detail view prints it with the pace.
import { FIVE_HOURS_MS, WEEK_MS, gaugeOf, span } from '@handline/app-core';
import type { Window } from '@handline/client';
import { useEffect, useState } from 'react';
import { Icon } from './ui/index.ts';

/** The time, moved on every minute so a reset countdown stays true on a page left open. */
export function useNow(everyMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}

const GAUGE = 'gauge grid min-w-0 grid-cols-[3.2em_minmax(3em,1fr)_3em_var(--icon)] items-center gap-x-2 gap-y-0.5';
const LABEL = 'gauge-label text-label text-ink-2';
const FILL = { ok: 'bg-ok', warn: 'bg-wait', bad: 'bg-bad' } as const;
const NOTE = 'col-[2/-1] flex items-center gap-1.5 text-label';

export const SPANS = { '5 h': FIVE_HOURS_MS, week: WEEK_MS } as const;

/** From this percent a window shows a warning glyph; at 100 it shows the full one. */
export const WARN_PCT = 80;

export function Gauge(props: { label: keyof typeof SPANS; window: Window | null; now: number; detail?: boolean }) {
  const { label, window: w, now, detail = false } = props;
  if (!w)
    return (
      <div className={GAUGE} data-gauge={label}>
        <span className={LABEL}>{label}</span>
        <span className="muted gauge-none col-[2/-1]">no reading</span>
      </div>
    );
  const g = gaugeOf(w, SPANS[label], now);
  const reset = g.resetIn === null ? undefined : `resets in ${span(g.resetIn)}`;
  return (
    <div className={GAUGE} data-gauge={label} data-tone={g.tone} title={reset}>
      <span className={LABEL}>{label}</span>
      <span
        className="gauge-bar relative h-2 overflow-hidden rounded-chip bg-raised"
        role="meter"
        aria-label={`${label} usage`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={g.pct}
      >
        <span className={`gauge-fill block h-full ${FILL[g.tone]}`} style={{ width: `${g.pct}%` }} />
        {g.through !== null ? (
          <span className="gauge-tick absolute inset-y-0 w-0.5 bg-ink opacity-60" style={{ left: `${g.through}%` }} aria-hidden />
        ) : null}
      </span>
      <span className="gauge-pct text-right font-mono text-label tabular-nums">{g.pct}%</span>
      <span className="gauge-glyph inline-flex text-bad">
        {g.pct >= 100 ? (
          <Icon name="full" size={14} label="Full" />
        ) : g.pct >= WARN_PCT ? (
          <Icon name="warn" size={14} label={`Over ${WARN_PCT} percent`} />
        ) : null}
      </span>
      {detail ? (
        <>
          {reset ? (
            <span className={`gauge-reset muted ${NOTE}`}>
              <Icon name="waiting" size={14} aria-hidden />
              {reset}
            </span>
          ) : null}
          <span className={`gauge-more muted ${NOTE}`}>
            <Icon name="stats" size={14} aria-hidden />
            {g.runway !== null ? `lasts about ${span(g.runway)} at this pace` : g.pct ? 'lasts to the reset' : ''} · {w.source}
          </span>
        </>
      ) : null}
    </div>
  );
}
