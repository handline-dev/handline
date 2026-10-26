// The status bar's usage item: the busiest account's 5 h figure, e.g. `5h 5%`; the accounts' figures and resets are on the page it opens.
import { useAccounts, useServes } from '@handline/app-data';

const WARN = 80;
const HOT = 95;

export function UsageLink() {
  const { data } = useAccounts();
  const { served } = useServes('accounts.list');
  if (!served) return null;
  const list = data?.accounts ?? [];
  const summary = list.map((a) => `${a.label} ${a.five_hour ? `${Math.round(a.five_hour.used_pct)}%` : '–'}`).join(' · ');
  const busiest = list.reduce<(typeof list)[number] | null>(
    (best, a) => (a.five_hour && (!best?.five_hour || a.five_hour.used_pct > best.five_hour.used_pct) ? a : best),
    null,
  )?.five_hour;
  const pct = busiest ? Math.round(busiest.used_pct) : null;
  return (
    <a
      className="usage usage-btn cursor-pointer text-ink-2 no-underline"
      href="#/stats"
      title={`Account usage (5 h): ${summary || 'none'}`}
      aria-label={`Account usage, 5 h: ${summary || 'none'}`}
    >
      <span className={`count ${pct === null ? '' : pct >= HOT ? 'text-bad' : pct >= WARN ? 'text-wait' : ''}`}>
        {pct === null ? '5h –' : `5h ${pct}%`}
      </span>
    </a>
  );
}
