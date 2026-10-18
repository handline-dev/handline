// The status bar's usage item and its popup: one line per account with the 5 h and week gauges, the agents on it and the runway. A line opens
// that account's page; the popup follows the accounts topic. Narrow widths drop the reset countdowns first, then the percentages (CSS).
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { capacityOf, headroom, span } from '@handline/app-core';
import { useAccounts, useOutlook, useServes } from '@handline/app-data';
import { agentsLine } from './Accounts.tsx';
import { Meter } from './RailIcons.tsx';
import { Gauge, useNow } from './Gauge.tsx';
import { Icon } from './ui/index.ts';

const ROW = 'usage-row flex flex-col gap-1.5 rounded-card border bg-panel px-3 py-2.5 text-inherit no-underline';
// A warning is also a word and a glyph on the row, not the edge alone.
const EDGE = { ok: 'border-line', warn: 'border-wait', bad: 'border-bad' } as const;
// Fixed, not absolute: the rail clips what overflows it; --pop-b/--pop-l put it above the button. On a phone it is a sheet along the bottom.
const POP =
  'usage-pop fixed bottom-[var(--pop-b)] left-[var(--pop-l)] z-20 flex overflow-y-auto max-h-[calc(100dvh_-_var(--pop-b)_-_8px)] w-[min(360px,calc(100vw_-_16px))] flex-col gap-2 rounded-card border border-line bg-panel p-2.5 shadow-[var(--shadow-pop)] max-sm:inset-x-0 max-sm:top-auto max-sm:bottom-0 max-sm:max-h-[calc(100dvh_-_8px)] max-sm:w-auto max-sm:rounded-b-none max-sm:rounded-t-dialog';

const POP_W = 360;
/** Above the button, its left edge kept inside the window (the button sits at the right of the bar). */
export function popPlace(button: { left: number; top: number }, vw: number, vh: number): { bottom: number; left: number } {
  const w = Math.min(POP_W, vw - 16);
  return { bottom: vh - button.top + 8, left: Math.max(8, Math.min(button.left, vw - w - 8)) };
}

export function UsagePopup() {
  const { data } = useAccounts();
  const { served } = useServes('accounts.list');
  const outlook = useOutlook('24h').data?.runway ?? [];
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState<CSSProperties>({});
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);
  const place = () => {
    const r = root.current?.getBoundingClientRect();
    if (!r) return;
    const p = popPlace(r, window.innerWidth, window.innerHeight);
    setAt({ '--pop-b': `${p.bottom}px`, '--pop-l': `${p.left}px` } as CSSProperties);
  };
  useEffect(() => {
    if (!open) return;
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [open]);
  const toggle = () => {
    place();
    setOpen(!open);
  };
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
    <div className="usage relative" ref={root}>
      <button
        className="usage-btn cursor-pointer border-0 bg-transparent p-0 text-ink-2"
        aria-expanded={open}
        onClick={toggle}
        title={`Account usage (5 h): ${summary || 'none'}`}
        aria-label={`Account usage, 5 h: ${summary || 'none'}`}
      >
        <Meter pct={worst} label="busiest account, 5 h" />
        <span className={`count ${!cap || cap.word === 'plenty' ? '' : cap.word === 'tight' ? 'text-wait' : 'text-bad'}`}>
          {cap ? `usage: ${cap.word}, ${cap.left}% left${runway ? `, out in ${runway}` : ''}` : 'usage: –'}
        </span>
      </button>
      {open ? (
        <div className={POP} style={at} role="dialog" aria-label="Account usage">
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
