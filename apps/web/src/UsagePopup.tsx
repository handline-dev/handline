// The status bar's usage item and its popup: one line per account with the 5 h and week gauges, the agents on it and the runway. A line opens
// that account's page; the popup follows the accounts topic. Narrow widths drop the reset countdowns first, then the percentages (CSS).
import { useState } from 'react';
import { gaugeOf, span } from '@handline/app-core';
import { useAccounts, useServes } from '@handline/app-data';
import { agentsLine } from './Accounts.tsx';
import { Meter } from './RailIcons.tsx';
import { Gauge, SPANS, useNow } from './Gauge.tsx';

export function UsagePopup() {
  const { data } = useAccounts();
  const { served } = useServes('accounts.list');
  const [open, setOpen] = useState(false);
  const now = useNow();
  if (!served) return null;
  const list = data?.accounts ?? [];
  const summary = list.map((a) => `${a.label} ${a.five_hour ? `${Math.round(a.five_hour.used_pct)}%` : '–'}`).join(' · ');
  const worst = Math.max(0, ...list.map((a) => Math.round(a.five_hour?.used_pct ?? 0)));
  return (
    <div className="usage">
      <button
        className="usage-btn"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        title={`Account usage (5 h): ${summary || 'none'}`}
        aria-label={`Account usage, 5 h: ${summary || 'none'}`}
      >
        <Meter pct={worst} label="busiest account, 5 h" />
        <span className="count">{list.length ? `${worst}%` : '–'}</span>
      </button>
      {open ? (
        <div className="usage-pop" role="dialog" aria-label="Account usage">
          {list.length ? (
            list.map((a) => {
              const run = a.five_hour ? gaugeOf(a.five_hour, SPANS['5 h'], now).runway : null;
              return (
                <a key={a.name} className="usage-row" href={`#/accounts/${encodeURIComponent(a.name)}`} onClick={() => setOpen(false)}>
                  <strong>{a.label}</strong>
                  <Gauge label="5 h" window={a.five_hour} now={now} />
                  <Gauge label="week" window={a.weekly} now={now} />
                  <span className="muted">
                    agents {agentsLine(a)}
                    {run !== null ? ` · runway ${span(run)}` : ''}
                  </span>
                </a>
              );
            })
          ) : (
            <p className="muted">No accounts configured.</p>
          )}
          <a className="usage-all" href="#/accounts" onClick={() => setOpen(false)}>
            All accounts
          </a>
        </div>
      ) : null}
    </div>
  );
}
