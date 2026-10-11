// The agent list. A row is an agent's only control surface: Start or Resume on the selected row, a ⋯ menu for stopping,
// and under the selected row the facts the old agent header showed.
import { useState } from 'react';
import {
  ago,
  controlsOf,
  dotOf,
  railGroups,
  shortName,
  type Dot,
  stopModesOf,
  asks,
  verbsOf,
  type Verb,
  badgesOf,
  type GroupBy,
  type KV,
  type StopMode,
} from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useConnection, useStore } from '@handline/app-react';
import { useAgentDetail, useAgents, useServes, useUnread, useVerbMark, useVerbRun, useWrite } from '@handline/app-data';
import { unserved, useServed, VerbSheet } from './AgentVerbs.tsx';
import { useHrCount } from './TeamPage.tsx';
import { Badges } from './Badges.tsx';
import { DOT_ORDER, Mark, MARKS, Meter } from './RailIcons.tsx';
import { useNow } from './Gauge.tsx';
import { Button, Icon, Popup } from './ui/index.ts';
import { setting } from './daemon.ts';
import {
  goAgent,
  goInbox,
  goSettings,
  goTasks,
  goTeam,
  goWorktrees,
  useAccountsRoute,
  useInboxRoute,
  usePageRoute,
  useTeamRoute,
  useWorktreesRoute,
} from './route.ts';
import { NewAgentButton } from './NewAgent.tsx';
import { View } from './View.tsx';

// One shape per nav row; the current one is raised with the inset bar.
const NAV = 'nav flex h-7 w-full items-center gap-2 rounded-ctl border-0 bg-transparent px-2 text-left text-ink hover:bg-raised';
const navOf = (on: boolean): string => (on ? `${NAV} on bg-raised shadow-[inset_2px_0_0_var(--ink)]` : NAV);

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
  const [by, setBy] = useState<GroupBy>(() => {
    const v = setting(storage, 'group', 'team').value;
    return v === 'state' || v === 'account' ? v : 'team';
  });
  const [hidden, setHidden] = useState<Set<Dot>>(
    () => new Set(setting(storage, 'hiddenDots', '').value.split(',').filter(Boolean) as Dot[]),
  );
  const [folded, setFolded] = useState<Set<string>>(() => new Set(setting(storage, 'folded', '').value.split('|').filter(Boolean)));
  const flip = <T extends string>(set: Set<T>, k: T, key: string, sep: string): Set<T> => {
    const next = new Set(set);
    if (!next.delete(k)) next.add(k);
    setting(storage, key, '').save([...next].join(sep));
    return next;
  };
  const groups = railGroups(agents, by, { hidden, search });
  const counts = new Map<Dot, number>();
  for (const a of agents) counts.set(dotOf(a), (counts.get(dotOf(a)) ?? 0) + 1);
  const unread = useUnread().length;
  const onInbox = useInboxRoute().open;
  const hr = useHrCount();
  const onTeam = !!useTeamRoute();
  const onAccounts = useAccountsRoute().open;
  const onWorktrees = useWorktreesRoute().open;
  const worktreesServed = useServes('worktrees.list').served;
  const pageRoute = usePageRoute();
  const onPage = onAccounts ? 'accounts' : pageRoute;
  return (
    <nav
      className="rail flex w-[var(--rail-w,280px)] flex-none flex-col overflow-hidden border-r border-line bg-panel min-h-0 max-[900px]:fixed max-[900px]:z-10 max-[900px]:[inset:0_auto_0_0] max-[900px]:w-[min(320px,90vw)] max-[900px]:shadow-[var(--shadow-pop)]"
      aria-label="Agents"
    >
      {/* The shell's drag strip: 38 px row centred on the traffic lights; `rail-top` is the tests' hook. */}
      <div className="rail-top flex items-center gap-0.5 pt-2 px-2.5 pb-1 shell:h-[38px] shell:py-0 shell:pl-[86px] shell:[-webkit-app-region:drag]">
        <Button className="shell:[-webkit-app-region:no-drag]" icon="sidebar" label="Hide the agent list (⌘B)" onClick={onToggle} />
        <Button className="shell:[-webkit-app-region:no-drag]" icon="back" label="Back (⌘[)" onClick={() => history.back()} />
        <Button className="shell:[-webkit-app-region:no-drag]" icon="forward" label="Forward (⌘])" onClick={() => history.forward()} />
        {unread ? (
          <button
            className="bell ml-auto border-0 rounded-[9px] bg-wait px-1.5 text-label text-canvas shell:[-webkit-app-region:no-drag]"
            title="Unread in the inbox"
            aria-label={`${unread} unread in the inbox`}
            onClick={goInbox}
          >
            {unread}
          </button>
        ) : null}
      </div>
      <div className="rail-nav flex flex-wrap gap-0.5 px-1.5">
        <button className={navOf(onInbox)} aria-current={onInbox ? 'page' : undefined} onClick={goInbox}>
          <Icon name="inbox" size={14} aria-hidden /> Inbox{unread ? ` ${unread}` : ''}
        </button>
        <button className={navOf(onTasks)} aria-current={onTasks ? 'page' : undefined} onClick={() => goTasks()}>
          <Icon name="tasks" size={14} aria-hidden /> Tasks
        </button>
        <button className={navOf(onTeam)} onClick={() => goTeam(hr ? 'hr' : 'team')}>
          <Icon name="team" size={14} aria-hidden /> Team{hr ? <span className="hr-dot text-bad"> ● HR {hr}</span> : null}
        </button>
        {worktreesServed ? (
          <button className={navOf(onWorktrees)} aria-current={onWorktrees ? 'page' : undefined} onClick={() => goWorktrees()}>
            <Icon name="worktrees" size={14} aria-hidden /> Worktrees
          </button>
        ) : null}
        <button className={navOf(onPage === 'accounts')} onClick={() => void (location.hash = '#/accounts')}>
          <Icon name="accounts" size={14} aria-hidden /> Accounts
        </button>
        <button className={navOf(onPage === 'stats')} onClick={() => void (location.hash = '#/stats')}>
          <Icon name="stats" size={14} aria-hidden /> Stats
        </button>
      </div>
      <div className="rail-opts flex flex-wrap items-center gap-2 border-b border-line px-2.5 py-2">
        <input
          className="search w-full max-w-none flex-[1_1_100%] rounded-ctl border border-line bg-canvas px-2 py-[5px]"
          type="search"
          placeholder="Search agents"
          aria-label="Search agents"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="rounded-ctl border border-line bg-canvas px-1 py-0.5"
          aria-label="Group by"
          value={by}
          onChange={(e) => {
            setBy(e.target.value as GroupBy);
            setting(storage, 'group', '').save(e.target.value);
          }}
        >
          {['team', 'state', 'account'].map((o) => (
            <option key={o} value={o}>
              by {o}
            </option>
          ))}
        </select>
        <div className="filters flex flex-wrap gap-0.5" role="group" aria-label="Show agents that are">
          {DOT_ORDER.map((d) => (
            <button
              key={d}
              className={`filter${hidden.has(d) ? '' : ' on'} inline-flex items-center gap-0.5 rounded-ctl border px-1 py-px text-hint text-ink-2 ${hidden.has(d) ? 'border-line bg-transparent opacity-45' : 'border-ink-2 bg-transparent'}`}
              aria-pressed={!hidden.has(d)}
              aria-label={`${MARKS[d].label}: ${counts.get(d) ?? 0}`}
              title={`${MARKS[d].label}: ${counts.get(d) ?? 0}. Click to ${hidden.has(d) ? 'show' : 'hide'}`}
              onClick={() => setHidden(flip(hidden, d, 'hiddenDots', ','))}
            >
              <Mark dot={d} title={MARKS[d].label} />
              <span className="count">{counts.get(d) ?? 0}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="rail-list min-h-0 flex-1 overflow-auto">
        {conn === 'open' ? null : <p className="muted pad">Connecting…</p>}
        <View q={list} served={served} shape="agents" what="the agent list" empty={conn === 'open' && !agents.length && 'No agents.'}>
          {groups.map((g) => (
            <section className="group" key={g.title}>
              <h3 className="flex items-center justify-between gap-1.5">
                <button
                  className="fold border-0 bg-transparent p-0 text-inherit [font:inherit] [letter-spacing:inherit] [text-transform:inherit]"
                  aria-expanded={!folded.has(g.title)}
                  onClick={() => setFolded(flip(folded, g.title, 'folded', '|'))}
                >
                  <span aria-hidden>{folded.has(g.title) ? '▸' : '▾'}</span> {g.title}
                </button>
                <GroupMarks rows={g.rows.map((r) => r.agent)} />
              </h3>
              {folded.has(g.title)
                ? null
                : g.rows.map(({ agent, depth }) => (
                    <Row
                      key={agent.name}
                      agent={agent}
                      depth={depth}
                      team={by === 'team' ? g.title : ''}
                      selected={agent.name === selected}
                    />
                  ))}
            </section>
          ))}
        </View>
      </div>
      <NewAgentButton />
      <footer className="rail-foot flex gap-0.5 border-t border-line px-2 py-1">
        <Button icon="settings" label="Settings" onClick={() => void (location.hash = '#/settings')} />
        <Button icon="help" label="Keys (?)" onClick={() => dispatchEvent(new KeyboardEvent('keydown', { key: '?' }))} />
      </footer>
    </nav>
  );
}

/** What a collapsed or open team says at a glance: one mark and count per state present. */
function GroupMarks({ rows }: { rows: AgentView[] }) {
  const n = new Map<Dot, number>();
  for (const a of rows) n.set(dotOf(a), (n.get(dotOf(a)) ?? 0) + 1);
  return (
    <span className="group-marks inline-flex gap-1.5 font-normal tracking-normal text-hint">
      {DOT_ORDER.filter((d) => n.get(d)).map((d) => (
        <span key={d} title={`${n.get(d)} ${MARKS[d].label}`}>
          <Mark dot={d} />
          {n.get(d)}
        </span>
      ))}
    </span>
  );
}

/** The minutes left in the prompt cache; '' when there is none on record, 'out' once it has lapsed. */
function cacheLeft(until: string | null, now: number): string {
  if (!until) return '';
  const m = Math.ceil((Date.parse(until) - now) / 60_000);
  return m <= 0 ? 'out' : `${m}m`;
}

/** The daemon's context figure for an agent, as a whole percent; absent until the view carries it. */
const ctxOf = (a: AgentView): number | null => {
  const v = (a as { ctx_pct?: number | null }).ctx_pct;
  return typeof v === 'number' ? Math.round(v) : null;
};

function Row({ agent: a, depth, team, selected }: { agent: AgentView; depth: number; team: string; selected: boolean }) {
  const store = useStore();
  const dot = dotOf(a);
  const [account, setAccount] = useState(''); // for the next start only
  const [ask, setAsk] = useState<Verb | null>(null);
  const mark = useVerbMark(a.name);
  const now = useNow();
  const cache = cacheLeft(a.cache_until, now);
  const ctx = ctxOf(a);
  const badges = badgesOf(a, store.me).filter((b) => b.kind !== 'no_terminal'); // the mark already says it
  const tip = [a.name, mark ?? a.state, ago(a.state_since), a.task ? `#${a.task.id} ${a.task.title}` : ''].filter(Boolean).join(' · ');
  // The left edge: the waiting hue outranks the selection.
  const edge = dot === 'waiting' ? 'border-l-wait' : selected ? 'border-l-primary' : 'border-l-transparent';
  return (
    <div className={`row-wrap${selected ? ' on bg-canvas' : ''}`} data-agent={a.name}>
      <div
        className={`row${selected ? ' on' : ''}${dot === 'waiting' ? ' attn' : ''} flex items-start gap-1 px-2.5 py-[7px] border-l-[3px] hover:bg-canvas ${edge}`}
        style={{ paddingLeft: 10 + depth * 14 }}
      >
        <button
          className="row-main flex min-w-0 flex-1 items-center gap-2 border-0 bg-transparent p-0 text-left"
          onClick={() => goAgent(a.name)}
          aria-current={selected ? 'true' : undefined}
          title={tip}
        >
          <Mark dot={dot} title={`${MARKS[dot].label} · ${ago(a.state_since)}`} />
          <span className="name min-w-0 flex-1 whitespace-nowrap">{shortName(a.name, team)}</span>
          <Badges badges={badges} />
          {a.task ? (
            <span className="task-ref flex-none text-hint text-ink-2" title={a.task.title}>
              #{a.task.id}
            </span>
          ) : null}
          {ctx !== null ? <Meter pct={ctx} label="context used" /> : null}
          {cache ? (
            <span
              className={`cache flex-none text-hint text-ink-2${cache === 'out' ? ' out line-through' : ''}`}
              title={cache === 'out' ? 'The prompt cache has lapsed' : `${cache} until the prompt cache lapses`}
            >
              {cache}
            </span>
          ) : null}
        </button>
        {selected ? <RowControls agent={a} account={account} setAccount={setAccount} onAsk={setAsk} /> : null}
      </div>
      {selected && ask ? <VerbSheet a={a} verb={ask} onClose={() => setAsk(null)} /> : null}
    </div>
  );
}

/** Start or Resume, and the ⋯ menu: the agent verbs the row's state allows, then the ways to stop. Which apply comes from the agent's detail. */
function RowControls({
  agent: a,
  account,
  setAccount,
  onAsk,
}: {
  agent: AgentView;
  account: string;
  setAccount: (a: string) => void;
  onAsk: (v: Verb) => void;
}) {
  const accounts = [...new Set((useAgents().data?.agents ?? []).map((x) => x.account).filter((x): x is string => !!x))];
  const agent = a.name;
  const store = useStore();
  const d = useAgentDetail(agent).data;
  const [open, setOpen] = useState(false);
  const start = useWrite('session.start', agent);
  const resume = useWrite('session.wake', agent);
  const stopWrite = useWrite('session.stop', agent);
  const interrupt = useWrite('session.interrupt', agent);
  const run = useVerbRun(agent);
  const served = useServed();
  const mark = useVerbMark(agent);
  const busy = start.isPending || resume.isPending || stopWrite.isPending || interrupt.isPending || !!mark;
  if (!d) return null;
  const c = controlsOf(d, store.me);
  const verb = (v: Verb) => {
    setOpen(false);
    if (asks(v) || v.id === 'move') onAsk(v);
    else run(v);
  };
  const stop = (mode: StopMode) => {
    setOpen(false);
    if (
      mode === 'kill' &&
      !confirm(
        `${d.structured ? `End the session of ${agent}` : `Kill the terminal of ${agent}`}?${a.task ? ` It has work in flight: ${a.task.title}.` : ''}`,
      )
    )
      return;
    if (mode === 'interrupt') interrupt.mutate({ agent });
    else stopWrite.mutate({ agent, mode });
  };
  const verbs = verbsOf(a, d);
  return (
    <span className="row-actions relative flex flex-none items-center gap-1">
      {c.start ? (
        <Button
          variant="solid"
          label="Start"
          disabled={busy}
          onClick={() => start.mutate(d.structured ? { agent } : { agent, account: account || undefined })}
        >
          {start.isPending ? 'starting…' : '▶ Start'}
        </Button>
      ) : null}
      {c.resume ? (
        <Button variant="outline" label="Resume" disabled={busy} onClick={() => resume.mutate({ agent })}>
          {resume.isPending ? 'resuming…' : '⟳ Resume'}
        </Button>
      ) : null}
      <Button
        label={`More actions for ${agent}`}
        aria-label={`More actions for ${agent}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {mark ?? (stopWrite.isPending || interrupt.isPending ? 'stopping…' : '⋯')}
      </Button>
      <Popup open={open} onClose={() => setOpen(false)} title={`More actions for ${agent}`} align="right">
        {c.start && !d.structured ? (
          <select
            aria-label="Account for this start"
            title={`The daemon would pick: ${d.account_pick.account ?? 'none'} (${d.account_pick.reason})`}
            value={account}
            onChange={(e) => setAccount(e.target.value)}
          >
            <option value="">{`start on auto: ${d.account_pick.account ?? 'none'}`}</option>
            {accounts.map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
          </select>
        ) : null}
        {verbs.map((v) => (
          <button
            key={v.id}
            type="button"
            className="ui-prow max-sm:min-h-12 disabled:cursor-not-allowed disabled:opacity-55"
            disabled={busy || !served(v)}
            title={served(v) ? undefined : unserved(v)}
            onClick={() => verb(v)}
          >
            <strong>{v.label}</strong>
            {served(v) ? null : <span className="muted"> not served</span>}
          </button>
        ))}
        <button className="ui-prow max-sm:min-h-12" onClick={() => goSettings(agent)}>
          <strong>Settings</strong>
        </button>
        {c.stop
          ? stopModesOf(d)
              .filter((m) => m.mode !== 'close' && !(d.structured && m.mode === 'interrupt'))
              .map((m) => (
                <button
                  key={m.mode}
                  type="button"
                  className={`ui-prow max-sm:min-h-12 disabled:cursor-not-allowed disabled:opacity-55${m.mode === 'kill' ? ' [&>strong]:text-bad' : ''}`}
                  disabled={busy}
                  onClick={() => stop(m.mode)}
                >
                  <strong>{m.label}</strong>
                  <span className="muted"> {m.hint}</span>
                </button>
              ))
          : null}
      </Popup>
    </span>
  );
}
