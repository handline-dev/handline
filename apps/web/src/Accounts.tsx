// The Accounts page (#/accounts, #/accounts/:name): one line per account with its gauges, state and agents, and one account in three tabs.
import { useState } from 'react';
import { STATE_WORD, outUntil, stateOf, span } from '@handline/app-core';
import type { Account } from '@handline/client';
import { useConnection } from '@handline/app-react';
import { useAccountUsage, useAccounts, useAgents, useServes, useWrite } from '@handline/app-data';
import { AccountLogin } from './AccountLogin.tsx';
import { AccountSettings } from './AccountSettings.tsx';
import { Gauge, useNow } from './Gauge.tsx';
import { goAgent } from './route.ts';
import { Button, Card, Dialog, Icon, ScrollPage, PageHead, PageTitle, TabStrip, type IconName } from './ui/index.ts';
import { View } from './View.tsx';

const TABS = ['Overview', 'Agents', 'Settings'] as const;
type Tab = (typeof TABS)[number];

// Out and login-needed read bad, in use reads ok; the chip's glyph and word say the same, so the hue is never the only sign.
const STATE_TONE =
  'data-[state=out]:border-bad data-[state=out]:text-bad data-[state=needs_login]:border-bad data-[state=needs_login]:text-bad data-[state=in_use]:border-ok data-[state=in_use]:text-ok';
const FACT = 'acct-fact m-0 flex items-center gap-2 [&_svg]:text-ink-3';
const ROW =
  'acct-row grid min-h-11 grid-cols-[minmax(8em,1.2fr)_auto_minmax(10em,1fr)_minmax(10em,1fr)_auto] items-center gap-x-4 gap-y-2 px-4 py-2 text-inherit no-underline hover:bg-raised max-[960px]:grid-cols-[minmax(8em,1fr)_auto_auto] max-[960px]:[&_.gauge]:col-span-full [&:not(:first-child)]:border-t [&:not(:first-child)]:border-line';

const STATE_ICON: Record<ReturnType<typeof stateOf>, IconName> = {
  needs_login: 'warn',
  out: 'full',
  in_use: 'working',
  idle: 'idle',
  unknown: 'help',
};

/** The state as a glyph and a word: the colour of the chip is never the only sign. */
export function StateChip({ account, now }: { account: Account; now: number }) {
  const state = stateOf(account);
  const until = state === 'out' ? outUntil(account) : null;
  return (
    <span className="acct-state-wrap inline-flex items-center gap-1.5">
      <Icon name={STATE_ICON[state]} size={14} label={STATE_WORD[state]} />
      <span
        className={`badge acct-state ${state} mr-1.5 inline-block rounded-card border border-line px-1.5 text-hint text-ink-2 ${STATE_TONE}`}
        data-state={state}
      >
        {state === 'out' && until ? `out until ${span(Date.parse(until) - now)} from now` : STATE_WORD[state]}
      </span>
    </span>
  );
}

export const agentsLine = (account: Account) => `${account.agents.length} / cap ${account.cap_effective}`;

const LOGIN_WORD: Record<Account['login_state'], string> = { ok: 'logged in', needs_login: 'needs login', unknown: 'login unknown' };
const resetAt = (iso: string) => new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

/** What `hl account list` adds to the gauges: the login, the plan, when the week resets, and whether it can take another agent and why not. */
export function UsageLine({ account }: { account: Account }) {
  const week = account.weekly?.resets_at;
  return (
    <span className="acct-usage col-span-full text-hint muted" data-usage={account.name}>
      {[LOGIN_WORD[account.login_state], account.plan, week ? `week resets ${resetAt(week)}` : null].filter(Boolean).join(' · ')}
      {account.assignable === undefined ? null : account.assignable ? (
        <span className="acct-assign"> · can take another agent</span>
      ) : (
        <span className="acct-assign no"> · cannot take another agent: {account.assign_reason}</span>
      )}
    </span>
  );
}

function Row({ account, now }: { account: Account; now: number }) {
  return (
    <a className={ROW} href={`#/accounts/${encodeURIComponent(account.name)}`} data-account={account.name}>
      <span className="acct-name flex min-w-0 items-baseline gap-2">
        <strong>{account.label}</strong>
        {account.label !== account.name ? <span className="muted mono">{account.name}</span> : null}
      </span>
      <StateChip account={account} now={now} />
      <Gauge label="5 h" window={account.five_hour} now={now} />
      <Gauge label="week" window={account.weekly} now={now} />
      <span
        className="acct-agents inline-flex items-center gap-1 font-mono text-label text-ink-2"
        title={`Agents on it, and its cap: ${agentsLine(account)}`}
      >
        <Icon name="agent" size={14} label="Agents" />
        {account.agents.length}/{account.cap_effective}
      </span>
      <UsageLine account={account} />
    </a>
  );
}

/** The daemon has no add call for an account, so adding one is a host command. */
function AddAccount() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button icon="plus" label="Add an account" onClick={() => setOpen(true)} />
      {open ? (
        <Dialog title="Add an account" onClose={() => setOpen(false)}>
          <p className="muted">The daemon adds accounts from the host. Run there:</p>
          <p className="mono">hl account add &lt;name&gt;</p>
        </Dialog>
      ) : null}
    </>
  );
}

export function AccountsPage({ name }: { name: string | null }) {
  const q = useAccounts();
  const usage = useAccountUsage().data?.accounts;
  const { served } = useServes('accounts.list');
  const now = useNow();
  // The topic's rows are live; the usage read adds the fields only it carries.
  const list = (q.data?.accounts ?? []).map((account) => ({ ...usage?.find((u) => u.name === account.name), ...account }));
  const one = name === null ? null : list.find((account) => account.name === name);
  return (
    <ScrollPage className="accounts" aria-label="Accounts">
      <PageHead className="accounts-head">
        {name === null ? (
          <PageTitle>Accounts</PageTitle>
        ) : (
          <>
            <Button className="back" icon="back" label="Back to accounts" onClick={() => (location.hash = '#/accounts')}>
              Accounts
            </Button>
            <PageTitle>{one?.label ?? name}</PageTitle>
          </>
        )}
        {name === null ? <AddAccount /> : null}
      </PageHead>
      <View
        q={q}
        served={served}
        shape="accounts"
        what="accounts"
        empty={!list.length && 'No accounts configured. Add one from the host with hl account add <name>.'}
      >
        {name === null ? (
          <Card className="acct-list flex flex-col">
            {list.map((account) => (
              <Row key={account.name} account={account} now={now} />
            ))}
          </Card>
        ) : one ? (
          <AccountPageOne account={one} now={now} />
        ) : (
          <div className="empty">
            <strong>This account no longer exists</strong>
            <p>
              <a href="#/accounts">Back to accounts</a>
            </p>
          </div>
        )}
      </View>
    </ScrollPage>
  );
}

function AccountPageOne({ account, now }: { account: Account; now: number }) {
  const [tab, setTab] = useState<Tab>('Overview');
  const agents = useAgents().data?.agents ?? [];
  return (
    <div className="acct-one mx-auto flex max-w-[var(--w-form)] flex-col gap-3">
      <p className="acct-head m-0 flex items-center gap-2">
        <StateChip account={account} now={now} />
      </p>
      <UsageLine account={account} />
      <TabStrip label="Account" value={tab} onChange={setTab} tabs={TABS.map((t) => ({ value: t, label: t }))} />
      {tab === 'Overview' ? (
        <div className="acct-overview flex flex-col gap-3">
          <Gauge label="5 h" window={account.five_hour} now={now} detail />
          <Gauge label="week" window={account.weekly} now={now} detail />
          <p className={FACT}>
            <Icon name="schedules" size={14} label="Month" />
            {account.cycle.on
              ? `ends ${account.cycle.end ?? 'at the next anniversary'}${account.cycle.end_is_default ? ' (the monthly anniversary)' : ''}`
              : 'renewing'}
          </p>
          <p className={FACT}>
            <Icon name="refresh" size={14} label="Usage recovery" />
            {account.recover ? 'recovers a dead read' : 'no recovery'}
          </p>
          <AccountLogin account={account} />
        </div>
      ) : null}
      {tab === 'Agents' ? (
        account.agents.length ? (
          <ul className="acct-agents-list">
            {account.agents.map((n) => {
              const v = agents.find((x) => x.name === n);
              return (
                <li key={n}>
                  <button className="nav rounded-ctl border-0 bg-transparent px-2 py-[5px]" onClick={() => goAgent(n)}>
                    {n}
                  </button>
                  <span className="muted"> {v ? v.state : ''}</span>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted">No agents are on this account.</p>
        )
      ) : null}
      {tab === 'Settings' ? <AccountSettings account={account} /> : null}
    </div>
  );
}
