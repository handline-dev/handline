// The status bar's usage item: the busiest account's 5 h figure and the time to its reset, e.g. `5h 5% · 3:20`. It opens the Usage page.
import { useAccounts, useServes } from '@handline/app-data';
import { useNow } from '#src/Gauge.tsx';

const WARN = 80;
const HOT = 95;

/** Time left as `h:mm`. */
const hhmm = (ms: number): string => {
  const m = Math.max(0, Math.ceil(ms / 60_000));
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
};

export function UsageLink() {
  const { data } = useAccounts();
  const { served } = useServes('accounts.list');
  const now = useNow();
  if (!served) return null;
  const list = data?.accounts ?? [];
  const summary = list.map((a) => `${a.label} ${a.five_hour ? `${Math.round(a.five_hour.used_pct)}%` : '–'}`).join(' · ');
  const busiest = list.reduce<(typeof list)[number] | null>(
    (best, a) => (a.five_hour && (!best?.five_hour || a.five_hour.used_pct > best.five_hour.used_pct) ? a : best),
    null,
  )?.five_hour;
  const pct = busiest ? Math.round(busiest.used_pct) : null;
  const reset = busiest?.resets_at ? Date.parse(busiest.resets_at) - now : null;
  return (
    <a
      className="usage usage-btn cursor-pointer text-ink-2 no-underline"
      href="#/accounts"
      title={`Account usage (5 h): ${summary || 'none'}`}
      aria-label={`Account usage, 5 h: ${summary || 'none'}`}
    >
      <span className={`count ${pct === null ? '' : pct >= HOT ? 'text-bad' : pct >= WARN ? 'text-wait' : ''}`}>
        {pct === null ? '5h –' : `5h ${pct}%${reset === null ? '' : ` · ${hhmm(reset)}`}`}
      </span>
    </a>
  );
}
