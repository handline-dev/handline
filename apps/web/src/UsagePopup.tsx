// The status bar's usage item and its popup: one line per account with the 5 h and week gauges, the agents on it and the runway. A line opens
// that account's page; the popup follows the accounts topic. Narrow widths drop the reset countdowns first, then the percentages (CSS).
import { useState } from 'react';
import { headroom, span } from '@handline/app-core';
import { useAccounts, useOutlook, useServes } from '@handline/app-data';
import { agentsLine } from './Accounts.tsx';
import { Meter } from './RailIcons.tsx';
import { Gauge, useNow } from './Gauge.tsx';
import { Icon } from './ui/index.ts';

const ROW = 'usage-row flex flex-col gap-1.5 rounded-card border bg-panel px-3 py-2.5 text-inherit no-underline';
// A warning is also a word and a glyph on the row, not the edge alone.
const EDGE = { ok: 'border-line', warn: 'border-wait', bad: 'border-bad' } as const;
// Fixed, not absolute: the rail clips what overflows it. On a phone it is a sheet along the bottom.
const POP =
  'usage-pop fixed bottom-10 left-2 z-20 flex w-[min(360px,calc(100vw_-_16px))] flex-col gap-2 rounded-card border border-line bg-panel p-2.5 shadow-[var(--shadow-pop)] max-sm:inset-x-0 max-sm:top-auto max-sm:bottom-0 max-sm:w-auto max-sm:rounded-b-none max-sm:rounded-t-dialog';

export function UsagePopup() {
  const { data } = useAccounts();
  const { served } = useServes('accounts.list');
  const outlook = useOutlook('24h').data?.runway ?? [];
  const [open, setOpen] = useState(false);
  const now = useNow();
  if (!served) return null;
  const list = data?.accounts ?? [];
  const summary = list.map((a) => `${a.label} ${a.five_hour ? `${Math.round(a.five_hour.used_pct)}%` : '–'}`).join(' · ');
  const worst = Math.max(0, ...list.map((a) => Math.round(a.five_hour?.used_pct ?? 0)));
  return (
    <div className="usage relative">
      <button
        className="usage-btn cursor-pointer border-0 bg-transparent p-0 text-ink-2"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        title={`Account usage (5 h): ${summary || 'none'}`}
        aria-label={`Account usage, 5 h: ${summary || 'none'}`}
      >
        <Meter pct={worst} label="busiest account, 5 h" />
        <span className="count">{list.length ? `${worst}%` : '–'}</span>
      </button>
      {open ? (
        <div className={POP} role="dialog" aria-label="Account usage">
          {list.length ? (
            list.map((a) => {
              const h = headroom(a.five_hour, outlook.find((r) => r.account === a.name)?.runs_out_in_seconds ?? null, now);
              return (
                <a
                  key={a.name}
                  className={`${ROW} ${EDGE[h.tone]}`}
                  data-tone={h.tone}
                  href={`#/accounts/${encodeURIComponent(a.name)}`}
                  onClick={() => setOpen(false)}
                >
                  <strong>{a.label}</strong>
                  <Gauge label="5 h" window={a.five_hour} now={now} />
                  <Gauge label="week" window={a.weekly} now={now} />
                  <span className="muted">agents {agentsLine(a)}</span>
                  {h.stopAt !== null ? (
                    <span className="usage-stop flex items-center gap-1.5">
                      {h.tone !== 'ok' ? <Icon name="warn" size={14} label={h.tone === 'bad' ? 'Running out' : 'Running low'} /> : null}
                      agents stop at {new Date(h.stopAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} (in{' '}
                      {span(h.stopAt - now)})
                    </span>
                  ) : null}
                </a>
              );
            })
          ) : (
            <p className="muted">No accounts configured.</p>
          )}
          <div className="flex justify-end gap-3">
            <a className="usage-stats" href="#/stats" onClick={() => setOpen(false)}>
              Stats
            </a>
            <a className="usage-all" href="#/accounts" onClick={() => setOpen(false)}>
              All accounts
            </a>
          </div>
        </div>
      ) : null}
    </div>
  );
}
