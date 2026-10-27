// The Accounts tab of Settings (#/accounts): one line per account to manage; #/accounts/:name is one account in three tabs. The gauges are the Usage page's (UsagePage.tsx).
import { useState, type ReactNode } from 'react';
import { STATE_WORD, agentsLine, outUntil, paceLine, stateOf, span, dateTime } from '@handline/app-core';
import type { Account } from '@handline/client';
import { useHello } from '@handline/app-react';
import { classify, sentence, useAccounts, useAccountUsage, useAgents, useOff, useServes, useWrite } from '@handline/app-data';
import { AccountLogin } from '#src/AccountLogin.tsx';
import { AccountSettings } from '#src/AccountSettings.tsx';
import { Gauge, useNow } from '#src/Gauge.tsx';
import { SettingsSection, SettingsShell } from '#src/SettingsShell.tsx';
import { goAgent, leave, useHistoryValue } from '#src/route.ts';
import {
  Button,
  Dialog,
  Field,
  Icon,
  NOTE,
  ScrollPage,
  PageHead,
  PageTitle,
  SettingsCard,
  TabStrip,
  type IconName,
} from '#src/ui/index.ts';
import { View } from '#src/View.tsx';
import { AnimatedList } from '#src/AnimatedList.tsx';

const TABS = ['Overview', 'Agents', 'Settings'] as const;

// Out and login-needed read bad, in use reads ok; the chip's glyph and word say the same, so the hue is never the only sign.
const STATE_TONE =
  'data-[state=out]:border-bad data-[state=out]:text-bad data-[state=needs_login]:border-bad data-[state=needs_login]:text-bad data-[state=in_use]:border-ok data-[state=in_use]:text-ok';
const FACT = 'acct-fact m-0 flex items-center gap-2 [&_svg]:text-ink-3';
const ROW =
  'acct-row grid min-h-11 grid-cols-[minmax(8em,1.2fr)_auto_minmax(12em,1fr)_minmax(12em,1fr)_auto] items-center gap-x-4 gap-y-2 px-4 py-2 text-inherit no-underline tint max-[1200px]:grid-cols-[minmax(8em,1fr)_auto_auto] max-[1200px]:[&_.gauge]:col-span-full';
const WRAP = '[&:not(:first-child)]:border-t [&:not(:first-child)]:border-line';

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
    <span className="acct-state-wrap inline-flex whitespace-nowrap items-center gap-1.5">
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

const LOGIN_WORD: Record<Account['login_state'], string> = { ok: 'logged in', needs_login: 'needs login', unknown: 'login unknown' };

/** Where the week stands against its pace line, in words; nothing when the account has no pace or pacing is off. */
function PaceLine({ account, short = false }: { account: Account; short?: boolean }) {
  const line = paceLine(account);
  const text = short ? paceLine(account, true) : line;
  return line ? (
    <span className={`acct-pace text-label text-ink-2${short ? ' truncate' : ''}`} title={line}>
      {text}
    </span>
  ) : null;
}

/** What `hl account list` adds to the gauges: the login, the plan, when the week resets, and whether it can take another agent and why not. */
export function UsageLine({ account }: { account: Account }) {
  const week = account.weekly?.resets_at;
  return (
    <span className="acct-usage col-span-full text-hint muted" data-usage={account.name}>
      {[LOGIN_WORD[account.login_state], account.plan, week ? `week resets ${dateTime(week)}` : null].filter(Boolean).join(' · ')}
      {account.assignable === undefined ? null : account.assignable ? (
        <span className="acct-assign"> · can take another agent</span>
      ) : (
        <span className="acct-assign no"> · cannot take another agent: {account.assign_reason}</span>
      )}
    </span>
  );
}

/** A stalled account: STALLED and its reason, and for the founder Wake (the row loses the stall at once; a refusal puts it back and says why). */
export function Stall({ account }: { account: Account }) {
  const founder = useHello()?.role === 'founder';
  const { served } = useServes('account.wake');
  const wake = useWrite('account.wake', account.name);
  if (!account.stalled && !wake.error) return null;
  return (
    <span className="acct-stall flex flex-wrap items-center gap-2 text-hint" data-stall={account.name}>
      {account.stalled ? (
        <>
          <span className="badge stalled inline-block rounded-card border border-bad px-1.5 text-bad">blocked</span>
          <span className="acct-stall-reason" title={account.stalled.detail || account.stalled.error}>
            {account.stalled.reason}
          </span>
          {founder && served ? (
            <Button
              variant="outline"
              size="sm"
              label={wake.isPending ? 'Waking…' : 'Wake'}
              title="End the stall so agents may be placed on this account again"
              disabled={wake.isPending}
              onClick={() => wake.mutate({ account: account.name })}
            />
          ) : null}
        </>
      ) : null}
      {wake.error ? (
        <span className="text-bad" role="alert">
          {sentence(classify(wake.error))}
        </span>
      ) : null}
    </span>
  );
}

export function UsageRow({ account, now }: { account: Account; now: number }) {
  return (
    <div className={WRAP}>
      <a className={ROW} href={`#/accounts/${encodeURIComponent(account.name)}`} data-account={account.name}>
        <span className="acct-name flex min-w-0 items-baseline gap-2">
          <strong>{account.label}</strong>
          {account.label !== account.name ? <span className="muted mono">{account.name}</span> : null}
        </span>
        <StateChip account={account} now={now} />
        <Gauge label="5 h" window={account.five_hour} now={now} />
        <span className="acct-week flex min-w-0 flex-col gap-0.5 max-[1200px]:col-span-full">
          <Gauge label="week" window={account.weekly} now={now} />
          <PaceLine account={account} short />
        </span>
        <span
          className="acct-agents inline-flex whitespace-nowrap items-center gap-1 font-mono text-label text-ink-2"
          title="Agents working on it now, and the most it takes at once"
        >
          <Icon name="agent" size={14} label="Agents" />
          {agentsLine(account)}
        </span>
        <UsageLine account={account} />
      </a>
      <div className="px-4 pb-2 empty:hidden">
        <Stall account={account} />
      </div>
    </div>
  );
}

/** Founder only: a name registers the account, which then needs a login (the topic brings the row in). */
function AddAccount() {
  const founder = useHello()?.role === 'founder';
  const off = useOff('account.add');
  const [asking, setAsking] = useState(false);
  const [name, setName] = useState('');
  const add = useWrite('account.add', name);
  if (!founder) return null;
  const close = () => (setAsking(false), setName(''), add.reset());
  return (
    <>
      <Button icon="plus" label="Add an account" disabled={!!off} title={off} onClick={() => setAsking(true)} />
      <AnimatedList>
        {asking ? (
          <Dialog
            key="add"
            title="Add an account"
            onClose={close}
            action={{
              label: add.isPending ? 'Adding…' : 'Add account',
              disabled: !name.trim() || add.isPending,
              onClick: () => add.mutate({ account: name.trim() }, { onSuccess: close }),
            }}
          >
            <Field label="Name" value={name} autoComplete="off" onChange={(e) => setName(e.target.value)} />
            {add.error ? (
              <p className={`${NOTE} text-bad`} role="alert">
                <Icon name="failed" size={14} label="Error" />
                {sentence(classify(add.error))}
              </p>
            ) : null}
          </Dialog>
        ) : null}
      </AnimatedList>
    </>
  );
}

/** The accounts the topic lists, each with the fields only the usage read carries. */
export function useAccountList() {
  const q = useAccounts();
  const usage = useAccountUsage().data?.accounts;
  const { served } = useServes('accounts.list');
  const now = useNow();
  // The topic's rows are live; the usage read adds the fields only it carries.
  const list = (q.data?.accounts ?? []).map((account) => ({ ...usage?.find((u) => u.name === account.name), ...account }));
  return { q, served, now, list };
}

/** One account to manage: its name, state, how much of the week is used and its cap; the row opens the account's own page. */
function ManageRow({ account, now }: { account: Account; now: number }) {
  return (
    <a
      className="acct-row flex min-h-11 items-center gap-x-4 px-4 py-2 text-inherit no-underline tint [&:not(:first-child)]:border-t [&:not(:first-child)]:border-line"
      href={`#/accounts/${encodeURIComponent(account.name)}`}
      data-account={account.name}
    >
      <span className="acct-name flex min-w-0 flex-1 items-baseline gap-2">
        <strong>{account.label}</strong>
        {account.label !== account.name ? <span className="muted mono">{account.name}</span> : null}
      </span>
      <StateChip account={account} now={now} />
      <span className="acct-week-pct tabular-nums text-ink-2">
        {account.weekly ? `week ${Math.round(account.weekly.used_pct)}%` : 'week –'}
      </span>
      <span className="acct-cap font-mono text-label text-ink-2">up to {account.cap_effective} at once</span>
    </a>
  );
}

export function AccountsPage({ name }: { name: string | null }) {
  const { q, served, now, list } = useAccountList();
  const one = name === null ? null : list.find((account) => account.name === name);
  return (
    <Frame name={name} title={one?.label ?? name}>
      <View q={q} served={served} shape="accounts" what="accounts" empty={!list.length && 'No accounts configured.'}>
        {name === null ? (
          <SettingsCard className="acct-list flex flex-col">
            {list.map((account) => (
              <ManageRow key={account.name} account={account} now={now} />
            ))}
          </SettingsCard>
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
    </Frame>
  );
}

/** The accounts list is a tab of the Settings area; one account is a page of its own with a way back. */
function Frame({ name, title, children }: { name: string | null; title: string | null; children: ReactNode }) {
  if (name === null)
    return (
      <SettingsShell tab="accounts" label="Accounts">
        <SettingsSection title="Accounts" action={<AddAccount />}>
          {children}
        </SettingsSection>
      </SettingsShell>
    );
  return (
    <ScrollPage className="accounts" aria-label="Accounts">
      <PageHead className="accounts-head" back={{ label: 'accounts', to: () => leave('#/accounts') }}>
        <PageTitle>{title}</PageTitle>
      </PageHead>
      {children}
    </ScrollPage>
  );
}

function AccountPageOne({ account, now }: { account: Account; now: number }) {
  const [saved, setTab] = useHistoryValue('account-tab');
  const tab = TABS.find((t) => t === saved) ?? 'Overview';
  const agents = useAgents().data?.agents ?? [];
  return (
    <div className="acct-one flex flex-col gap-3">
      <p className="acct-head m-0 flex items-center gap-2">
        <StateChip account={account} now={now} />
      </p>
      <UsageLine account={account} />
      <Stall account={account} />
      <TabStrip label="Account" value={tab} onChange={(t) => setTab(t)} tabs={TABS.map((t) => ({ value: t, label: t }))} />
      {tab === 'Overview' ? (
        <div className="acct-overview flex flex-col gap-3">
          <Gauge label="5 h" window={account.five_hour} now={now} detail />
          <Gauge label="week" window={account.weekly} now={now} detail />
          <PaceLine account={account} />
          <p className={FACT}>
            <Icon name="schedules" size={14} label="Month" />
            {account.cycle.on ? (account.cycle.end ? `ends ${account.cycle.end}` : 'ends when its login drops') : 'renewing'}
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
