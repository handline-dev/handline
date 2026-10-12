// The agent list. A row shows an agent's state, its queue and a fold button when it has children; its actions (Start or Resume, the ⋯ menu)
// show while the pointer is on it or it is selected. One filter button opens the menu of what to show.
import { useState } from 'react';
import {
  ago,
  dotOf,
  railRows,
  shortName,
  teamOf,
  visibleRows,
  queueOf,
  RAIL_FILTERS,
  RAIL_STATE_WORD,
  type Dot,
  type Verb,
  badgesOf,
  type KV,
} from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useConnection, useStore } from '@handline/app-react';
import { useAgents, useUnreached, useServes, useUnread, useVerbMark } from '@handline/app-data';
import { VerbSheet } from './AgentVerbs.tsx';
import { useHrCount } from './TeamPage.tsx';
import { Badges } from './Badges.tsx';
import { Mark, MARKS, Meter } from './RailIcons.tsx';
import { useNow } from './Gauge.tsx';
import { Button, Icon, Popup, PROW } from './ui/index.ts';
import { setting } from './daemon.ts';
import {
  goAgent,
  goInbox,
  goTasks,
  goTeam,
  goWorktrees,
  useAccountsRoute,
  useInboxRoute,
  usePageRoute,
  useTeamRoute,
  useWorktreesRoute,
} from './route.ts';
import { RowControls } from './RowControls.tsx';
import { NewAgentButton } from './NewAgent.tsx';
import { View } from './View.tsx';
import { navOf, PairPhone } from './NavRows.tsx';

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
  const [hidden, setHidden] = useState<Set<Dot>>(
    () => new Set(setting(storage, 'hiddenDots', '').value.split(',').filter(Boolean) as Dot[]),
  );
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () => new Set(setting(storage, 'collapsed', '').value.split('|').filter(Boolean)),
  );
  const [menu, setMenu] = useState(false);
  const flip = <T extends string>(set: Set<T>, k: T, key: string, sep: string): Set<T> => {
    const next = new Set(set);
    if (!next.delete(k)) next.add(k);
    setting(storage, key, '').save([...next].join(sep));
    return next;
  };
  const unreached = useUnreached();
  const rows = visibleRows(railRows(agents, { hidden, search }), collapsed);
  const names = agents.map((x) => x.name);
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
          <Icon name="team" size={14} aria-hidden /> Team
          {hr ? (
            <span className="hr-dot text-bad">
              {' '}
              <Icon name="decision" size={12} aria-hidden /> HR {hr}
            </span>
          ) : null}
        </button>
        {worktreesServed ? (
          <button className={navOf(onWorktrees)} aria-current={onWorktrees ? 'page' : undefined} onClick={() => goWorktrees()}>
            <Icon name="worktrees" size={14} aria-hidden /> Worktrees
          </button>
        ) : null}
        <button className={navOf(onPage === 'accounts')} onClick={() => void (location.hash = '#/accounts')}>
          <Icon name="accounts" size={14} aria-hidden /> Accounts
        </button>
        <PairPhone />
        <button className={navOf(onPage === 'stats')} onClick={() => void (location.hash = '#/stats')}>
          <Icon name="stats" size={14} aria-hidden /> Stats
        </button>
      </div>
      <div className="rail-opts relative flex items-center gap-1.5 border-b border-line px-2.5 py-2">
        <input
          className="search min-w-0 flex-1 rounded-ctl border border-line bg-canvas px-2 py-[5px]"
          type="search"
          placeholder="Search agents"
          aria-label="Search agents"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Button
          icon="filter"
          label={hidden.size ? `Filter: ${hidden.size} hidden` : 'Filter'}
          aria-haspopup="menu"
          aria-expanded={menu}
          className={hidden.size ? 'text-primary' : ''}
          onClick={() => setMenu(!menu)}
        />
        <Popup open={menu} onClose={() => setMenu(false)} title="Show agents that are" align="right">
          {RAIL_FILTERS.map((f) => {
            const on = f.dots.some((d) => !hidden.has(d));
            const n = f.dots.reduce((t, d) => t + (counts.get(d) ?? 0), 0);
            return (
              <button
                key={f.id}
                type="button"
                role="menuitemcheckbox"
                aria-checked={on}
                className={`${PROW} filter`}
                onClick={() => {
                  const next = new Set(hidden);
                  for (const d of f.dots) on ? next.add(d) : next.delete(d);
                  setting(storage, 'hiddenDots', '').save([...next].join(','));
                  setHidden(next);
                }}
              >
                <span className="w-3.5 flex-none">{on ? <Icon name="markRead" size={12} aria-hidden /> : null}</span>
                <Mark dot={f.dots[0]!} title={MARKS[f.dots[0]!].label} />
                <span className="flex-1">{f.label}</span>
                <span className="muted count">{n}</span>
              </button>
            );
          })}
        </Popup>
      </div>
      <div className="rail-list min-h-0 flex-1 overflow-auto">
        {conn === 'open' ? null : <p className="muted pad">Connecting…</p>}
        <View q={list} served={served} shape="agents" what="the agent list" empty={conn === 'open' && !agents.length && 'No agents.'}>
          {rows.map(({ agent, depth, kids }) => (
            <Row
              key={agent.name}
              agent={agent}
              depth={depth}
              kids={kids}
              team={teamOf(agent.name, names)}
              folded={collapsed.has(agent.name)}
              onFold={() => setCollapsed(flip(collapsed, agent.name, 'collapsed', '|'))}
              unreached={unreached.get(agent.name) ?? null}
              selected={agent.name === selected}
            />
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

function Row({
  agent: a,
  depth,
  kids,
  team,
  folded,
  onFold,
  unreached,
  selected,
}: {
  agent: AgentView;
  depth: number;
  kids: number;
  team: string;
  folded: boolean;
  onFold: () => void;
  unreached: number | null;
  selected: boolean;
}) {
  const store = useStore();
  const dot = dotOf(a);
  const [account, setAccount] = useState(''); // for the next start only
  const [ask, setAsk] = useState<Verb | null>(null);
  const [hot, setHot] = useState(false); // the pointer or focus is on the row: its actions show, and only then do they cost a detail read
  const [menuOpen, setMenuOpen] = useState(false);
  const mark = useVerbMark(a.name);
  const now = useNow();
  const cache = cacheLeft(a.cache_until, now);
  const ctx = ctxOf(a);
  const queue = queueOf(a, unreached);
  const badges = badgesOf(a, store.me).filter((b) => b.kind !== 'no_terminal'); // the word already says it
  const word = RAIL_STATE_WORD[dot];
  const tip = [a.name, mark ?? a.state, ago(a.state_since), a.task ? `#${a.task.id} ${a.task.title}` : ''].filter(Boolean).join(' · ');
  // The left edge: the waiting hue outranks the selection.
  const edge = dot === 'waiting' ? 'border-l-wait' : selected ? 'border-l-primary' : 'border-l-transparent';
  const showControls = selected || hot || menuOpen;
  return (
    <div
      className={`row-wrap relative${selected ? ' on bg-canvas' : ''}`}
      data-agent={a.name}
      onPointerEnter={() => setHot(true)}
      onPointerLeave={() => setHot(false)}
      onFocus={() => setHot(true)}
      onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && setHot(false)}
    >
      <div
        className={`row${selected ? ' on' : ''}${dot === 'waiting' ? ' attn' : ''} flex items-start gap-1 px-2.5 py-[7px] border-l-[3px] hover:bg-canvas ${edge}`}
        style={{ paddingLeft: 10 + depth * 14 }}
      >
        {kids ? (
          <button
            className="fold flex-none border-0 bg-transparent p-0 text-ink-2"
            aria-expanded={!folded}
            aria-label={`${folded ? 'Show' : 'Hide'} the ${kids} under ${a.name}`}
            title={`${folded ? 'Show' : 'Hide'} the ${kids} under ${a.name}`}
            onClick={onFold}
          >
            <Icon name={folded ? 'chevronRight' : 'more'} size={12} aria-hidden />
          </button>
        ) : (
          <span className="w-3 flex-none" aria-hidden />
        )}
        <button
          className="row-main flex min-w-0 flex-1 items-center gap-2 border-0 bg-transparent p-0 text-left"
          onClick={() => goAgent(a.name)}
          aria-current={selected ? 'true' : undefined}
          title={tip}
        >
          <Mark dot={dot} title={`${MARKS[dot].label} · ${ago(a.state_since)}`} />
          <span className="name min-w-0 flex-1 whitespace-nowrap">{shortName(a.name, team)}</span>
          <Badges badges={badges} />
          <span className="state-word flex-none text-hint text-ink-2">{word}</span>
          {queue.messages ? (
            <span
              className="q-msgs inline-flex flex-none items-center gap-0.5 text-hint text-ink-2"
              title={`${queue.messages} messages not yet delivered`}
            >
              <Icon name="inbox" size={12} aria-hidden />
              {queue.messages}
            </span>
          ) : null}
          {queue.tasks ? (
            <span
              className="q-tasks inline-flex flex-none items-center gap-0.5 text-hint text-ink-2"
              title={`${queue.tasks} tasks waiting or in progress`}
            >
              <Icon name="tasks" size={12} aria-hidden />
              {queue.tasks}
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
        {showControls ? <RowControls agent={a} account={account} setAccount={setAccount} onAsk={setAsk} onMenu={setMenuOpen} /> : null}
      </div>
      {ask ? <VerbSheet a={a} verb={ask} onClose={() => setAsk(null)} /> : null}
    </div>
  );
}
