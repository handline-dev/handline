// The Usage page's accounts: one line each under hairlines, in the order the daemon lists them: name, state, plan, its agents, the 5-hour and
// week left, and a warning when the account runs out or is blocked. The settings of an account are in Settings.
import { dateTime, lostByAccount, paceLine, stallWord, stateOf, runwayWords } from '@handline/app-core';
import type { Account, Outlook } from '@handline/client';
import { useServes } from '@handline/app-data';
import { StateChip, useAccountList } from '#src/Accounts.tsx';
import { Icon } from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

const left = (w: Account['five_hour']): string => (w ? `${Math.max(0, Math.round(100 - w.used_pct))}% left` : 'not started');

/** What to watch for on the account: a stall, a full window, the pace that runs it out; nothing when it is fine. */
function warning(a: Account, runway: Outlook['runway'][number] | undefined): string | null {
  if (a.stalled) return stallWord(a.stalled);
  if (stateOf(a) === 'out') return null;
  if (runway && runway.runs_out_in_seconds !== null) return runwayWords(runway);
  const pace = a.pace && a.pace.state !== 'behind' && a.pace.state !== 'off' ? paceLine(a, true) : null;
  return pace;
}

function Line({ a, now, runway, lost }: { a: Account; now: number; runway?: Outlook['runway'][number]; lost?: number }) {
  const warn = warning(a, runway);
  return (
    <li className="usage-account flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-line py-4" data-account={a.name}>
      <strong className="min-w-16 text-lg">{a.label}</strong>
      <StateChip account={a} now={now} />
      {a.plan ? <span className="muted">{a.plan}</span> : null}
      {a.agents.length ? <span className="muted">{a.agents.length === 1 ? '1 agent' : `${a.agents.length} agents`}</span> : null}
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
    </li>
  );
}

export function UsageAccounts({ outlook }: { outlook?: Outlook }) {
  const lost = outlook ? lostByAccount(outlook) : [];
  const { q, served, now, list } = useAccountList();
  const { served: usageServed } = useServes('accounts.list');
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
