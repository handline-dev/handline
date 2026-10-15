// The agent list. A row shows an agent's state, its queue and a fold button when it has children; its actions (Start, the ⋯ menu)
// show while the pointer is on it or it is selected. One filter button opens the menu of what to show.
import { useEffect, useRef, useState } from 'react';
import {
  ago,
  dotOf,
  railRows,
  rowName,
  teamOf,
  visibleRows,
  queueOf,
  RAIL_FILTERS,
  type Dot,
  type Verb,
  badgesOf,
  type KV,
  taskRef,
} from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useConnection, useStore } from '@handline/app-react';
import { useAgents, useUnreached, useServes, useUnread, useVerbMark } from '@handline/app-data';
import { useMyTurn } from './MyTurn.tsx';
import { DeleteSheet, VerbSheet } from './AgentVerbs.tsx';
import { useHrCount } from './TeamPage.tsx';
import { Badges } from './Badges.tsx';
import { Mark, MARKS } from './RailIcons.tsx';
import { AgentGauges } from './AgentGauges.tsx';
import { useNow } from './Gauge.tsx';
import { Button, Icon, Menu, type At, MenuButton, MenuCheckItem, MenuContent, MenuTrigger } from './ui/index.ts';
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
import { AgentMenu, RowControls } from './RowControls.tsx';
import { NewAgentButton } from './NewAgent.tsx';
import { View } from './View.tsx';
import { navOf, PairPhone } from './NavRows.tsx';

// What a row's one line holds, in px: the state mark, the name's least width, a chip's icon and padding, a letter of it, and the gap between items.
const MARK_W = 14;
const NAME_MIN = 72;
const CHIP_PAD = 28;
const CHAR_W = 7;
const GAP = 8;

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
  const myTurn = useMyTurn().cards.length;
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
      {/* The shell's drag strip: row as tall as a tab strip, centred on the traffic lights (--h-tabs and --lights-w follow the zoom: shell.ts); `rail-top` is the tests' hook. */}
      <div className="rail-top flex items-center gap-0.5 pt-2 px-2.5 pb-1 shell:h-[var(--h-tabs)] shell:py-0 shell:pl-[var(--lights-w,86px)] shell:[-webkit-app-region:drag]">
        <Button
          className="shell:[-webkit-app-region:no-drag] shell:!h-[calc(var(--h-tabs)-4px)] shell:!w-[calc(var(--h-tabs)-4px)]"
          icon="sidebar"
          label="Hide the agent list (⌘B)"
          onClick={onToggle}
        />
        <Button
          className="shell:[-webkit-app-region:no-drag] shell:!h-[calc(var(--h-tabs)-4px)] shell:!w-[calc(var(--h-tabs)-4px)]"
          icon="back"
          label="Back (⌘[)"
          onClick={() => history.back()}
        />
        <Button
          className="shell:[-webkit-app-region:no-drag] shell:!h-[calc(var(--h-tabs)-4px)] shell:!w-[calc(var(--h-tabs)-4px)]"
          icon="forward"
          label="Forward (⌘])"
          onClick={() => history.forward()}
        />
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
        <button
          className={navOf(onPage === 'my-turn')}
          aria-current={onPage === 'my-turn' ? 'page' : undefined}
          onClick={() => void (location.hash = '#/my-turn')}
        >
          <Icon name="decision" size={14} aria-hidden /> My turn{myTurn ? ` ${myTurn}` : ''}
        </button>
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
        <MenuButton
          icon="filter"
          label={hidden.size ? `Filter: ${hidden.size} hidden` : 'Filter'}
          name="Show agents that are"
          className={hidden.size ? 'text-primary' : ''}
        >
          {RAIL_FILTERS.map((f) => {
            const on = f.dots.some((d) => !hidden.has(d));
            const n = f.dots.reduce((t, d) => t + (counts.get(d) ?? 0), 0);
            return (
              <MenuCheckItem
                key={f.id}
                checked={on}
                icon={<Mark dot={f.dots[0]!} title={MARKS[f.dots[0]!].label} />}
                onCheckedChange={() => {
                  const next = new Set(hidden);
                  for (const d of f.dots) {
                    if (on) next.add(d);
                    else next.delete(d);
                  }
                  setting(storage, 'hiddenDots', '').save([...next].join(','));
                  setHidden(next);
                }}
              >
                <span className="flex-1">{f.label}</span>
                <span className="muted count">{n}</span>
              </MenuCheckItem>
            );
          })}
        </MenuButton>
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

/** The minutes left in the prompt cache; null when none is on record, 0 or less once it has lapsed. */
const cacheLeft = (until: string | null, now: number): number | null => (until ? Math.ceil((Date.parse(until) - now) / 60_000) : null);

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
  const [ask, setAsk] = useState<Verb | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [hot, setHot] = useState(false); // the pointer or focus is on the row: its actions show, and only then do they cost a detail read
  const [menuOpen, setMenuOpen] = useState(false);
  const mark = useVerbMark(a.name);
  // The needs-you chip comes right after the name (the lesser chips drop first) and is the word when the word fits beside the name, else the icon.
  const main = useRef<HTMLButtonElement>(null);
  const [wide, setWide] = useState(true);
  const kindLen = a.needs_you?.kind.length ?? 0;
  useEffect(() => {
    const el = main.current;
    if (!el) return;
    const fit = () => setWide(el.clientWidth >= MARK_W + NAME_MIN + CHIP_PAD + CHAR_W * kindLen + 2 * GAP);
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [kindLen]);
  const now = useNow();
  const cache = cacheLeft(a.cache_until, now);
  const ctx = ctxOf(a);
  const queue = queueOf(a, unreached);
  const badges = badgesOf(a, store.me).filter((b) => b.kind !== 'no_terminal'); // the word already says it
  const tip = [a.name, mark ?? a.state, ago(a.state_since), a.task ? `${taskRef(a.task.id)} ${a.task.title}` : '']
    .filter(Boolean)
    .join(' · ');
  // The left edge: the waiting hue outranks the selection.
  const edge = dot === 'waiting' ? 'border-l-wait' : selected ? 'border-l-primary' : 'border-l-transparent';
  const showControls = selected || hot || menuOpen;
  // The ⋯ button asks the row for its own context menu, at the button.
  const opener = useRef<((at: At) => void) | null>(null);
  const openMenu = (at: At) => opener.current?.(at);
  return (
    <>
      <Menu onOpenChange={setMenuOpen} openRef={opener}>
        <MenuTrigger asChild>
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
              {/* One line tall and wrapping: a chip that does not fit drops below the box whole, never cut off. */}
              <button
                ref={main}
                className="row-main flex h-5 min-w-0 flex-1 flex-wrap content-start items-center gap-x-2 overflow-hidden border-0 bg-transparent p-0 text-left"
                onClick={() => goAgent(a.name)}
                aria-current={selected ? 'true' : undefined}
                title={tip}
              >
                <Mark dot={dot} title={`${MARKS[dot].label} · ${ago(a.state_since)}`} />
                <span className="name h-5 min-w-[4.5rem] flex-1 overflow-hidden text-ellipsis whitespace-nowrap leading-5">
                  {rowName(a.name, depth, team)}
                </span>
                {a.needs_you ? (
                  <span
                    className="needs inline-flex flex-none items-center gap-1 whitespace-nowrap rounded-pill bg-wait px-1.5 text-label text-canvas"
                    title={[a.needs_you.kind, a.needs_you.text].filter(Boolean).join(': ')}
                  >
                    <Icon name={a.needs_you.kind === 'permission' ? 'permission' : 'answer'} size={12} label={a.needs_you.kind} />
                    {wide ? <span>{a.needs_you.kind}</span> : null}
                  </span>
                ) : null}
                <Badges badges={badges} />
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
                    title={`${queue.tasks} tasks queued or doing`}
                  >
                    <Icon name="tasks" size={12} aria-hidden />
                    {queue.tasks}
                  </span>
                ) : null}
                <AgentGauges ctx={ctx} cacheMin={cache} />
              </button>
              {/* Laid over the row's right end, not beside the name: the row is the same height with or without them. */}
              {showControls ? (
                <div className="absolute right-1 top-1/2 -translate-y-1/2 bg-canvas">
                  <RowControls agent={a} onMenu={openMenu} />
                </div>
              ) : null}
            </div>
          </div>
        </MenuTrigger>
        <MenuContent>
          <AgentMenu agent={a} onAsk={setAsk} onDelete={() => setDeleting(true)} />
        </MenuContent>
      </Menu>
      {ask ? <VerbSheet a={a} verb={ask} onClose={() => setAsk(null)} /> : null}
      {deleting ? <DeleteSheet a={a} onClose={() => setDeleting(false)} /> : null}
    </>
  );
}
