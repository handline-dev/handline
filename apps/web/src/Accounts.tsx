// The Accounts page (#/accounts, #/accounts/:name): one line per account with its gauges, state and agents, and one account in three tabs.
import { useState } from 'react';
import { STATE_WORD, outUntil, stateOf, span } from '@handline/app-core';
import type { Account } from '@handline/client';
import { useConnection } from '@handline/app-react';
import { useAccounts, useAgents, useServes, useWrite } from '@handline/app-data';
import { AccountSettings } from './AccountSettings.tsx';
import { Gauge, useNow } from './Gauge.tsx';
import { goAgent } from './route.ts';
import { Button, Dialog, Icon, type IconName } from './ui/index.ts';
import { View } from './View.tsx';

const TABS = ['Overview', 'Agents', 'Settings'] as const;
type Tab = (typeof TABS)[number];

const STATE_ICON: Record<ReturnType<typeof stateOf>, IconName> = {
  needs_login: 'warn',
  out: 'full',
  in_use: 'working',
  idle: 'idle',
  unknown: 'help',
};

/** The state as a glyph and a word: the colour of the chip is never the only sign. */
export function StateChip({ a, now }: { a: Account; now: number }) {
  const state = stateOf(a);
  const until = state === 'out' ? outUntil(a) : null;
  return (
    <span className="acct-state-wrap">
      <Icon name={STATE_ICON[state]} size={14} label={STATE_WORD[state]} />
      <span className={`badge acct-state ${state}`} data-state={state}>
        {state === 'out' && until ? `out until ${span(Date.parse(until) - now)} from now` : STATE_WORD[state]}
      </span>
    </span>
  );
}

export const agentsLine = (a: Account) => `${a.agents.length} / cap ${a.cap_effective}`;

function Row({ a, now }: { a: Account; now: number }) {
  return (
    <a className="acct-row" href={`#/accounts/${encodeURIComponent(a.name)}`} data-account={a.name}>
      <span className="acct-name">
        <strong>{a.label}</strong>
        {a.label !== a.name ? <span className="muted mono">{a.name}</span> : null}
      </span>
      <StateChip a={a} now={now} />
      <Gauge label="5 h" window={a.five_hour} now={now} />
      <Gauge label="week" window={a.weekly} now={now} />
      <span className="acct-agents" title={`Agents on it, and its cap: ${agentsLine(a)}`}>
        <Icon name="agent" size={14} label="Agents" />
        {a.agents.length}/{a.cap_effective}
      </span>
    </a>
  );
}

/** The daemon has no add or remove call for an account (only login, alias, cap and the month), so adding one is a host command. */
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
  const { served } = useServes('accounts.list');
  const now = useNow();
  const list = q.data?.accounts ?? [];
  const one = name === null ? null : list.find((a) => a.name === name);
  return (
    <section className="accounts" aria-label="Accounts">
      <header className="accounts-head">
        {name === null ? (
          <h2>Accounts</h2>
        ) : (
          <>
            <Button className="back" icon="back" label="Back to accounts" onClick={() => (location.hash = '#/accounts')}>
              Accounts
            </Button>
            <h2>{one?.label ?? name}</h2>
          </>
        )}
        {name === null ? <AddAccount /> : null}
      </header>
      <View
        q={q}
        served={served}
        shape="accounts"
        what="accounts"
        empty={!list.length && 'No accounts configured. Add one from the host with hl account add <name>.'}
      >
        {name === null ? (
          <div className="acct-list">
            {list.map((a) => (
              <Row key={a.name} a={a} now={now} />
            ))}
          </div>
        ) : one ? (
          <AccountPageOne a={one} now={now} />
        ) : (
          <div className="empty">
            <strong>This account no longer exists</strong>
            <p>
              <a href="#/accounts">Back to accounts</a>
            </p>
          </div>
        )}
      </View>
    </section>
  );
}

function AccountPageOne({ a, now }: { a: Account; now: number }) {
  const [tab, setTab] = useState<Tab>('Overview');
  const agents = useAgents().data?.agents ?? [];
  return (
    <div className="acct-one">
      <p className="acct-head">
        <StateChip a={a} now={now} />
      </p>
      <div role="tablist" className="chat-tabs" aria-label="Account">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`chat-tab${tab === t ? ' on' : ''}`} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>
      {tab === 'Overview' ? (
        <div className="acct-overview">
          <Gauge label="5 h" window={a.five_hour} now={now} detail />
          <Gauge label="week" window={a.weekly} now={now} detail />
          <p className="acct-fact">
            <Icon name="schedules" size={14} label="Month" />
            {a.cycle.on
              ? `ends ${a.cycle.end ?? 'at the next anniversary'}${a.cycle.end_is_default ? ' (the monthly anniversary)' : ''}`
              : 'renewing'}
          </p>
          <p className="acct-fact">
            <Icon name="refresh" size={14} label="Usage recovery" />
            {a.recover ? 'recovers a dead read' : 'no recovery'}
          </p>
          <Login a={a} />
        </div>
      ) : null}
      {tab === 'Agents' ? (
        a.agents.length ? (
          <ul className="acct-agents-list">
            {a.agents.map((n) => {
              const v = agents.find((x) => x.name === n);
              return (
                <li key={n}>
                  <button className="nav" onClick={() => goAgent(n)}>
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
      {tab === 'Settings' ? <AccountSettings account={a} /> : null}
    </div>
  );
}

/**
 * Login: the daemon opens a terminal for the vendor's own flow, so the web never holds a credential. Embedding a terminal tab of an account is
 * not built (the terminal tab belongs to an agent's workspace), so the page names the terminal and the host command. Not offered on a phone.
 */
function Login({ a }: { a: Account }) {
  const login = useWrite('account.login', a.name);
  const { served } = useServes('account.login');
  const open = useConnection() === 'open';
  const terminal = login.data?.terminal;
  const inProgress = !!terminal && a.login_state !== 'ok';
  const off = !open ? 'daemon not connected' : served ? undefined : 'this daemon does not serve account.login';
  return (
    <div className="acct-login">
      <Button
        className="desktop-only"
        label={login.isPending ? '…' : a.login_state === 'ok' ? 'Log in again' : 'Log in'}
        disabled={!!off || login.isPending || inProgress}
        title={off ?? `Or from the host: hl login ${a.name}`}
        onClick={() => login.mutate({ account: a.name })}
      />
      <span className="muted phone-only">Log in from a computer.</span>
      {inProgress ? (
        <p className="muted" role="status">
          Login in progress in terminal <span className="mono">{terminal}</span>.
        </p>
      ) : null}
    </div>
  );
}
