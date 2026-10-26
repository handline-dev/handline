// The status bar's usage item: how much of the week is left across the accounts and whether it lasts to the reset at the current pace
// ("Week 62% left · lasts" / "runs out Thu 05:00"). It turns amber when the week runs out within the warning hours of Settings and says so once
// per crossing in a notice. The hover lists each account's week; the link opens the Usage page.
import { useEffect, useRef } from 'react';
import { alertText, forecastWords, runsOutSoon, span, weekForecast, type WeekForecast } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { useAccounts, useOutlook, useServes } from '@handline/app-data';

export const WEEK_ALERT_KEY = 'week_alert_hours';
export const WEEK_ALERT_DEFAULT = 24;

/** Hours before the week runs out at which the bar warns; 0 is off. Kept in this browser. */
export function weekAlertHours(): number {
  try {
    const v = localStorage.getItem(WEEK_ALERT_KEY);
    return v === null ? WEEK_ALERT_DEFAULT : Math.max(0, Number(v) || 0);
  } catch {
    return WEEK_ALERT_DEFAULT;
  }
}

const day = (ms: number) => new Date(ms).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
const pctOf = (f: WeekForecast) => (f.leftPct === null ? 'Week –' : `Week ${f.leftPct}% left`);

export function UsageLink() {
  const store = useStore();
  const { data } = useAccounts();
  const { data: outlook } = useOutlook('7d');
  const { served } = useServes('accounts.list');
  const list = data?.accounts ?? [];
  const now = Date.now();
  const f = weekForecast(list, outlook, now);
  const soon = runsOutSoon(f, weekAlertHours());
  const told = useRef(false);
  // once per crossing: a new notice only after the warning has cleared and come back
  useEffect(() => {
    if (!soon) told.current = false;
    else if (!told.current) {
      told.current = true;
      store.notices.push('info', alertText(f));
    }
  }, [soon, store, f]);
  if (!served) return null;
  const rows = list.map((a) => {
    const left = a.weekly ? `${Math.round(100 - a.weekly.used_pct)}% left` : 'no reading';
    const reset = a.weekly?.resets_at ? `, resets in ${span(Date.parse(a.weekly.resets_at) - now)}` : '';
    return `${a.label}: week ${left}${reset}`;
  });
  const busiest = list.reduce<(typeof list)[number] | null>(
    (best, a) => (a.five_hour && (!best?.five_hour || a.five_hour.used_pct > best.five_hour.used_pct) ? a : best),
    null,
  );
  const five = busiest?.five_hour ? [`5-hour window, ${busiest.label}: ${Math.round(busiest.five_hour.used_pct)}% used`] : [];
  const words = forecastWords(f, day);
  const title = [`Week across all accounts: ${pctOf(f)}, ${words}`, ...rows, ...five].join('\n');
  const tone = soon ? 'text-wait' : '';
  return (
    <a
      className="usage usage-btn cursor-pointer text-ink-2 no-underline"
      href="#/stats"
      title={title}
      aria-label={title.replaceAll('\n', '. ')}
    >
      <span className={`count ${tone}`} data-warn={soon || undefined}>
        {pctOf(f)}
        {f.kind === 'lasts' ? ' · lasts' : f.kind === 'runs_out' ? ` · runs out ${day(f.runsOutAt!)}` : ''}
      </span>
    </a>
  );
}
