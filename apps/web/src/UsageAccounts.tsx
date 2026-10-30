// The Usage page's accounts: one line each under hairlines, in the order the daemon lists them: name, state, plan, its agents, the 5-hour and
// week left, and a warning when the account runs out or is blocked. An account that needs a login says that and offers Log in, no windows.
// The settings of an account are in Settings.
import { useState } from 'react';
import { STATE_WORD, dateTime, lostByAccount, outUntil, runwayWords, span, stallWord, stateOf } from '@handline/app-core';
import type { Account, Outlook } from '@handline/client';
import { useServes } from '@handline/app-data';
import { AccountLogin } from '#src/AccountLogin.tsx';
import type { useAccountList } from '#src/Accounts.tsx';
import { goAgent } from '#src/route.ts';
import { Icon, Popup, PopupRow, type IconName } from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

const left = (w: Account['five_hour']): string => (w ? `${Math.max(0, Math.round(100 - w.used_pct))}% left` : 'not started');

// A glyph that stays still: the state is read from the word, the glyph only helps.
const STATE_ICON: Record<ReturnType<typeof stateOf>, IconName> = {
  needs_login: 'warn',
  out: 'full',
  in_use: 'agent',
  idle: 'idle',
  unknown: 'help',
};

/** What to watch for on the account: a stall, or the pace that runs it out (the daemon's); nothing when it lasts. */
function warning(a: Account, runway: Outlook['runway'][number] | undefined): string | null {
  if (a.stalled) return stallWord(a.stalled);
  return runway && runway.runs_out_in_seconds !== null && stateOf(a) !== 'out' ? runwayWords(runway) : null;
}

/** "3 agents ⌄": opens the agents on the account, each a link to its chat. */
function Agents({ a }: { a: Account }) {
  const [open, setOpen] = useState(false);
  if (!a.agents.length) return null;
  return (
    <span className="usage-agents relative">
      <button
        className="muted inline-flex items-center gap-1 border-0 bg-transparent p-0"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {a.agents.length === 1 ? '1 agent' : `${a.agents.length} agents`}
        <Icon name="chevron" size={12} aria-hidden />
      </button>
      <Popup open={open} onClose={() => setOpen(false)} title={`Agents on ${a.label}`}>
        {a.agents.map((name) => (
          <PopupRow key={name} label={name} onClick={() => goAgent(name)} />
        ))}
      </Popup>
    </span>
  );
}

function Line({ a, now, runway, lost }: { a: Account; now: number; runway?: Outlook['runway'][number]; lost?: number }) {
  const state = stateOf(a);
  const until = state === 'out' ? outUntil(a) : null;
  const warn = warning(a, runway);
  const word = until
    ? `Out until ${span(Date.parse(until) - now)} from now`
    : STATE_WORD[state][0]!.toUpperCase() + STATE_WORD[state].slice(1);
  return (
    <li className="usage-account flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line py-4" data-account={a.name}>
      <strong className="min-w-16 text-lg">{a.label}</strong>
      <span className="usage-state inline-flex items-center gap-1.5" data-state={state}>
        <Icon name={STATE_ICON[state]} size={14} label={word} />
        {word}
      </span>
      {state === 'needs_login' ? <AccountLogin account={a} /> : <Agents a={a} />}
      {a.plan ? <span className="muted">{a.plan}</span> : null}
      {state === 'needs_login' ? null : (
        <>
          <span className="tabular-nums">
            5-hour {left(a.five_hour)} · Week {left(a.weekly)}
          </span>
          {a.weekly?.resets_at ? <span className="muted">week resets {dateTime(a.weekly.resets_at)}</span> : null}
          {lost !== undefined ? <span className="usage-lost muted tabular-nums">{Math.round(lost)}% lost at this pace</span> : null}
          {warn ? (
            <span className="usage-warn inline-flex items-center gap-1 text-wait">
              <Icon name="warn" size={14} label="Warning" />
              {warn}
            </span>
          ) : null}
        </>
      )}
    </li>
  );
}

export function UsageAccounts({ accounts, outlook }: { accounts: ReturnType<typeof useAccountList>; outlook?: Outlook }) {
  const { q, served, now, list } = accounts;
  const { served: usageServed } = useServes('accounts.list');
  const lost = outlook ? lostByAccount(outlook) : [];
  return (
    <View q={q} served={served && usageServed} shape="accounts" what="the accounts">
      {list.length ? (
        <ul className="usage-accounts m-0 mb-8 list-none border-t border-line p-0" aria-label="Accounts">
          {list.map((a) => (
            <Line
              key={a.name}
              a={a}
              now={now}
              runway={outlook?.runway.find((r) => r.account === a.name)}
              lost={lost.find((l) => l.account === a.name)?.pct}
            />
          ))}
        </ul>
      ) : null}
    </View>
  );
}
