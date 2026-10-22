// The status bar's usage item: the busiest account's 5 h gauge and the capacity word. It opens the Usage page.
import { capacityOf, headroom, span } from '@handline/app-core';
import { useAccounts, useOutlook, useServes } from '@handline/app-data';
import { Meter } from '#src/RailIcons.tsx';
import { useNow } from '#src/Gauge.tsx';

export function UsageLink() {
  const { data } = useAccounts();
  const { served } = useServes('accounts.list');
  const outlook = useOutlook('24h').data?.runway ?? [];
  const now = useNow();
  if (!served) return null;
  const list = data?.accounts ?? [];
  const summary = list.map((a) => `${a.label} ${a.five_hour ? `${Math.round(a.five_hour.used_pct)}%` : '–'}`).join(' · ');
  const worst = Math.max(0, ...list.map((a) => Math.round(a.five_hour?.used_pct ?? 0)));
  const cap = capacityOf(list);
  const stops = (cap?.usable ?? []).flatMap(
    (a) => headroom(a.five_hour, outlook.find((r) => r.account === a.name)?.runs_out_in_seconds ?? null, now).stopAt ?? [],
  );
  const runway = stops.length ? span(Math.min(...stops) - now) : null;
  return (
    <a
      className="usage usage-btn cursor-pointer text-ink-2 no-underline"
      href="#/accounts"
      title={`Account usage (5 h): ${summary || 'none'}`}
      aria-label={`Account usage, 5 h: ${summary || 'none'}`}
    >
      <Meter pct={worst} label="busiest account, 5 h" />
      <span className={`count ${!cap || cap.word === 'plenty' ? '' : cap.word === 'tight' ? 'text-wait' : 'text-bad'}`}>
        {cap ? `usage: ${cap.word}, ${cap.left}% left${runway ? `, out in ${runway}` : ''}` : 'usage: –'}
      </span>
    </a>
  );
}
