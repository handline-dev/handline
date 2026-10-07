// One usage window as a bar: the fill in the tone of its percentage, a tick for how far through the window the clock is, and the numbers
// as text so colour is never the only signal.
import { FIVE_HOURS_MS, WEEK_MS, gaugeOf, span } from '@handline/app-core';
import type { Window } from '@handline/client';
import { useEffect, useState } from 'react';

/** The time, moved on every minute so a reset countdown stays true on a page left open. */
export function useNow(everyMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}

export const SPANS = { '5 h': FIVE_HOURS_MS, week: WEEK_MS } as const;

export function Gauge(props: { label: keyof typeof SPANS; window: Window | null; now: number; detail?: boolean }) {
  const { label, window: w, now, detail = false } = props;
  if (!w)
    return (
      <div className="gauge" data-gauge={label}>
        <span className="gauge-label">{label}</span>
        <span className="muted">no reading</span>
      </div>
    );
  const g = gaugeOf(w, SPANS[label], now);
  return (
    <div className="gauge" data-gauge={label} data-tone={g.tone}>
      <span className="gauge-label">{label}</span>
      <span className="gauge-bar" role="meter" aria-label={`${label} usage`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={g.pct}>
        <span className="gauge-fill" style={{ width: `${g.pct}%` }} />
        {g.through !== null ? <span className="gauge-tick" style={{ left: `${g.through}%` }} aria-hidden /> : null}
      </span>
      <span className="gauge-pct">{g.pct}%</span>
      <span className="gauge-reset muted">{g.resetIn === null ? '' : `resets in ${span(g.resetIn)}`}</span>
      {detail ? (
        <span className="gauge-more muted">
          {g.runway !== null ? `lasts about ${span(g.runway)} at this pace` : g.pct ? 'lasts to the reset' : ''} · {w.source}
        </span>
      ) : null}
    </div>
  );
}
