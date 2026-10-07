// The agent list. A row is an agent's only control surface: Start or Resume on the selected row, a ⋯ menu for stopping,
// and under the selected row the facts the old agent header showed.
import { useRef, useState } from 'react';
import {
  ago,
  controlsOf,
  dotOf,
  railGroups,
  STOP_MODES,
  asks,
  verbsOf,
  type Verb,
  badgesOf,
  type GroupBy,
  type KV,
  type StopMode,
} from '@handline/app-core';
import type { AgentDetail, AgentView } from '@handline/client';
import { useConnection, useSelector, useStore } from '@handline/app-react';
import { useAgentDetail, useAgents, useServes, useUnread, useWrite } from '@handline/app-data';
import { unserved, useServed, useVerbMark, useVerbRun, VerbSheet } from './AgentVerbs.tsx';
import { useHrCount } from './TeamPage.tsx';
import { Badges } from './Badges.tsx';
import { UsagePopup } from './UsagePopup.tsx';
import { setting } from './daemon.ts';
import { goAgent, goInbox, goSettings, goTasks, goTeam, useInboxRoute, useTeamRoute } from './route.ts';
import { View } from './View.tsx';

export function Sidebar({
  selected,
  onTasks,
  storage,
  onToggle,
}: {
  selected: string;
  onTasks: boolean;
  storage: KV | null;
  onToggle: () => void;
}) {
  const list = useAgents();
  const { served } = useServes('agents.list');
  const agents = list.data?.agents ?? [];
  const conn = useConnection();
  const [search, setSearch] = useState('');
  const [by, setBy] = useState(() => setting(storage, 'group', 'role').value as GroupBy);
  const [hideParked, setHide] = useState(() => setting(storage, 'hideParked', '').value === '1');
  const groups = railGroups(agents, by, { hideParked, search });
  const unread = useUnread().length;
  const onInbox = useInboxRoute().open;
  const dot = conn === 'open' ? 'running' : conn === 'closed' ? 'blocked' : 'waiting';
  const hr = useHrCount();
  const onTeam = !!useTeamRoute();
  return (
    <nav className="rail" aria-label="Agents">
      <div className="rail-top">
        <button className="icon" aria-label="Hide the agent list" onClick={onToggle}>
          ☰
        </button>
        <strong className="brand">Handline</strong>
        {unread ? (
          <button className="bell" title="Unread in the inbox" aria-label={`${unread} unread in the inbox`} onClick={goInbox}>
            {unread}
          </button>
        ) : null}
      </div>
      <div className="rail-nav">
        <button className={`nav${onInbox ? ' on' : ''}`} aria-current={onInbox ? 'page' : undefined} onClick={goInbox}>
          Inbox{unread ? ` ${unread}` : ''}
        </button>
        <button className="nav" aria-current={onTasks ? 'page' : undefined} onClick={() => goTasks()}>
          Tasks
        </button>
        <button className={`nav${onTeam ? ' on' : ''}`} onClick={() => goTeam(hr ? 'hr' : 'team')}>
          Team{hr ? <span className="hr-dot"> ● HR {hr}</span> : null}
        </button>
        <a className="nav" href="#/accounts">
          Accounts
        </a>
      </div>
      <div className="rail-opts">
        <input
          className="search"
          type="search"
          placeholder="Search agents"
          aria-label="Search agents"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          aria-label="Group by"
          value={by}
          onChange={(e) => {
            setBy(e.target.value as GroupBy);
            setting(storage, 'group', '').save(e.target.value);
          }}
        >
          {['role', 'state', 'account'].map((o) => (
            <option key={o} value={o}>
              by {o}
            </option>
          ))}
        </select>
        <label className="check">
          <input
            type="checkbox"
            checked={hideParked}
            onChange={(e) => {
              setHide(e.target.checked);
              setting(storage, 'hideParked', '').save(e.target.checked ? '1' : '');
            }}
          />{' '}
          hide parked
        </label>
      </div>
      <div className="rail-list">
        {conn === 'open' ? null : <p className="muted pad">Connecting…</p>}
        <View q={list} served={served} shape="agents" what="the agent list" empty={conn === 'open' && !agents.length && 'No agents.'}>
          {groups.map((g) => (
            <section className="group" key={g.title}>
              <h3>{g.title}</h3>
              {g.rows.map(({ agent, depth }) => (
                <Row key={agent.name} agent={agent} depth={depth} selected={agent.name === selected} />
              ))}
            </section>
          ))}
        </View>
      </div>
      <footer className="rail-status">
        <span>
          <span className={`dot ${dot}`} /> daemon {conn}
        </span>
        <span>
          {agents.length} agents · {agents.filter((a) => a.state === 'running').length} running
        </span>
        <UsagePopup />
      </footer>
    </nav>
  );
}

function Row({ agent: a, depth, selected }: { agent: AgentView; depth: number; selected: boolean }) {
  const store = useStore();
  const dot = dotOf(a);
  const [account, setAccount] = useState(''); // for the next start only
  const [ask, setAsk] = useState<Verb | null>(null);
  const mark = useVerbMark(a.name);
  return (
    <div className={`row-wrap${selected ? ' on' : ''}`} data-agent={a.name}>
      <div className={`row${selected ? ' on' : ''}${dot === 'waiting' ? ' attn' : ''}`} style={{ paddingLeft: 10 + depth * 14 }}>
        <button className="row-main" onClick={() => goAgent(a.name)} aria-current={selected ? 'true' : undefined}>
          <span className={`dot ${dot}`} title={a.state} />
          <span className="row-text">
            <span className="name">{a.name}</span>
            <span className="sub">
              <Badges badges={badgesOf(a, store.me)} />
              {mark ?? a.state} · {ago(a.state_since)}
              {a.task ? ` · #${a.task.id} ${a.task.title}` : ''}
            </span>
          </span>
          {selected ? null : <span className="row-side">{[a.model, a.account].filter(Boolean).join(' · ')}</span>}
        </button>
        {selected ? <RowControls agent={a} account={account} onAsk={setAsk} /> : null}
      </div>
      {selected && ask ? <VerbSheet a={a} verb={ask} onClose={() => setAsk(null)} /> : null}
      {selected ? <Detail agent={a} account={account} setAccount={setAccount} /> : null}
    </div>
  );
}

/** Start or Resume, and the ⋯ menu: the agent verbs the row's state allows, then the ways to stop. Which apply comes from the agent's detail. */
function RowControls({ agent: a, account, onAsk }: { agent: AgentView; account: string; onAsk: (v: Verb) => void }) {
  const agent = a.name;
  const store = useStore();
  const d = useAgentDetail(agent).data;
  const menu = useRef<HTMLDetailsElement>(null);
  const start = useWrite('session.start', agent);
  const resume = useWrite('session.restart', agent);
  const stopWrite = useWrite('session.stop', agent);
  const interrupt = useWrite('session.interrupt', agent);
  const run = useVerbRun(agent);
  const served = useServed();
  const mark = useVerbMark(agent);
  const busy = start.isPending || resume.isPending || stopWrite.isPending || interrupt.isPending || !!mark;
  if (!d) return null;
  const c = controlsOf(d, store.me);
  const verb = (v: Verb) => {
    menu.current?.removeAttribute('open');
    if (asks(v) || v.id === 'move') onAsk(v);
    else run(v);
  };
  const stop = (mode: StopMode) => {
    menu.current?.removeAttribute('open');
    if (mode === 'kill' && !confirm(`Kill the terminal of ${agent}?${a.task ? ` It has work in flight: ${a.task.title}.` : ''}`)) return;
    if (mode === 'interrupt') interrupt.mutate({ agent });
    else stopWrite.mutate({ agent, mode });
  };
  const verbs = verbsOf(a, d);
  return (
    <span className="row-actions">
      {c.start ? (
        <button className="btn primary" disabled={busy} onClick={() => start.mutate({ agent, account: account || undefined })}>
          {start.isPending ? 'starting…' : '▶ Start'}
        </button>
      ) : null}
      {c.resume ? (
        <button className="btn" disabled={busy} onClick={() => resume.mutate({ agent })}>
          {resume.isPending ? 'resuming…' : '⟳ Resume'}
        </button>
      ) : null}
      <details className="menu" ref={menu}>
        <summary className="btn" aria-label={`More actions for ${agent}`}>
          {mark ?? (stopWrite.isPending || interrupt.isPending ? 'stopping…' : '⋯')}
        </summary>
        <div className="menu-list">
          {verbs.map((v) => (
            <button
              key={v.id}
              className="menu-item"
              disabled={busy || !served(v)}
              title={served(v) ? undefined : unserved(v)}
              onClick={() => verb(v)}
            >
              <strong>{v.label}</strong>
              {served(v) ? null : <span className="muted"> not served</span>}
            </button>
          ))}
          {c.stop
            ? STOP_MODES.filter((m) => m.mode !== 'close').map((m) => (
                <button
                  key={m.mode}
                  className={`menu-item${m.mode === 'kill' ? ' danger' : ''}`}
                  disabled={busy}
                  onClick={() => stop(m.mode)}
                >
                  <strong>{m.label}</strong>
                  <span className="muted"> {m.hint}</span>
                </button>
              ))
            : null}
        </div>
      </details>
    </span>
  );
}

function Detail({ agent: a, account, setAccount }: { agent: AgentView; account: string; setAccount: (a: string) => void }) {
  const store = useStore();
  const q = useAgentDetail(a.name);
  const d: AgentDetail | undefined = q.data;
  const { served } = useServes('agent.get');
  const accounts = [...new Set((useAgents().data?.agents ?? []).map((x) => x.account).filter((x): x is string => !!x))];
  const c = d ? controlsOf(d, store.me) : null;
  const row = (k: string, v: string | null | undefined) =>
    v ? (
      <div className="kv" key={k}>
        <span className="muted">{k}</span>
        <span>{v}</span>
      </div>
    ) : null;
  return (
    <div className="row-detail" aria-label={`${a.name} details`}>
      {a.block ? (
        <div className="banner">
          Blocked: {a.block.kind}
          {a.wall_reason ? ` (${a.wall_reason})` : ''} since {ago(a.block.since)}
        </div>
      ) : null}
      {row('model', [a.model, a.effort].filter(Boolean).join(' · '))}
      {row('account', a.account)}
      {c?.start && d ? (
        <div className="kv">
          <span className="muted">start on</span>
          <select
            aria-label="Account for this start"
            title={`The daemon would pick: ${d.account_pick.account ?? 'none'} (${d.account_pick.reason})`}
            value={account}
            onChange={(e) => setAccount(e.target.value)}
          >
            <option value="">{`auto: ${d.account_pick.account ?? 'none'}`}</option>
            {accounts.map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <View q={q} served={served} shape="agentDetail" what={`${a.name} details`}>
        {null}
      </View>
      {row('task', a.task ? `#${a.task.id} ${a.task.title}` : null)}
      {row('worktree', d?.worktree?.branch)}
      {row('session', d?.session_id)}
      {row('terminal', d?.terminal)}
      {row('cache until', a.cache_until ? new Date(a.cache_until).toLocaleTimeString() : null)}
      <button className="btn" onClick={() => goSettings(a.name)}>
        Settings
      </button>
      <Timeline agent={a.name} />
    </div>
  );
}

/** What happened to the agent since this page opened. */
function Timeline({ agent }: { agent: string }) {
  const notes = useSelector((s) => s.notes.get(agent) ?? []);
  if (!notes.length) return null;
  return (
    <details className="timeline">
      <summary className="muted">Timeline ({notes.length})</summary>
      {notes
        .slice()
        .reverse()
        .map((n, i) => (
          <div className="note" key={i}>
            <span className="muted">{ago(n.at)}</span> {n.text}
          </div>
        ))}
    </details>
  );
}
