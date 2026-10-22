// The agent list. A row shows an agent's state, its queue and a fold button when it has children; its actions (Start, the ⋯ menu)
// show while the pointer is on it or it is selected. One filter button opens the menu of what to show.
import { Fragment, useEffect, useState, type ReactNode } from 'react';
import {
  chipChanges,
  chipCounts,
  chipOf,
  dotOf,
  hiddenDots,
  navList,
  teamOf,
  togglePin as toggleIn,
  RAIL_FILTERS,
  taskRef,
  type Dot,
  type KV,
  type NavGroup,
} from '@handline/app-core';
import type { AgentView, NavPrefsChanges } from '@handline/client';
import { useConnection } from '@handline/app-react';
import { useAgents, useHrCount, useNav, useServes, useUnreached, useWorktrees } from '@handline/app-data';
import { useMyTurn } from '#src/MyTurn.tsx';
import { Mark, MARKS } from '#src/RailIcons.tsx';
import { Button, Icon, MenuButton, MenuCheckItem, MenuItem, MenuLabel, MenuSeparator } from '#src/ui/index.ts';
import { setting } from '#src/daemon.ts';
import {
  goTasks,
  goTeam,
  useAccountsRoute,
  usePageRoute,
  useTeamRoute,
} from '#src/route.ts';
import { AgentRow, liveOf } from '#src/AgentRow.tsx';
import { NewAgentButton } from '#src/NewAgent.tsx';
import { View } from '#src/View.tsx';
import { navOf } from '#src/NavRows.tsx';

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
  const { nav, ready, change, togglePin: togglePinAgent } = useNav();
  const hidden = hiddenDots(nav.hidden);
  const collapsed = new Set(nav.collapsed);
  useMoveSettings(storage, ready && !!list.data, agents.map((x) => x.name), change);
  // The view menu: the list under its teams, and the line under a name that says what the agent is doing.
  const [grouped, setGrouped] = useState(() => setting(storage, 'groupTeams', '1').value !== '0');
  const [activity, setActivity] = useState(() => setting(storage, 'activityLines', '1').value !== '0');
  const toggle = (key: string, on: boolean, set: (on: boolean) => void) => {
    setting(storage, key, '').save(on ? '1' : '0');
    set(on);
  };
  const unreached = useUnreached();
  // Where an agent works, for the line under its name when it has no task: the repo and branch of the worktree it owns.
  const trees = useWorktrees();
  const where = new Map<string, string>();
  for (const w of trees.data?.worktrees ?? []) if (w.owner && !where.has(w.owner)) where.set(w.owner, `${w.repo}/${w.branch}`);
  const chip = chipOf(nav);
  const chipN = chipCounts(agents);
  const names = agents.map((x) => x.name);
  const tree = nav.layout === 'tree';
  const nl = navList(agents, nav, search);
  const rows = tree ? byTeam(nl.rows, grouped, names) : nl.rows;
  const heads = tree && grouped;
  // A team's heading counts the rows under it, a child with its parent.
  const teamSize = new Map<string, number>();
  let blockTeam = '';
  for (const { agent, depth } of rows) {
    if (depth === 0) blockTeam = teamOf(agent.name, names);
    teamSize.set(blockTeam, (teamSize.get(blockTeam) ?? 0) + 1);
  }
  const row = (agent: AgentView, depth: number, kids: number, team: string, key?: string) => (
    <AgentRow
      key={key}
      agent={agent}
      depth={depth}
      kids={kids}
      team={team}
      sub={lastLine(agent) || (agent.task ? `${taskRef(agent.task.id)} ${agent.task.title}` : (where.get(agent.name) ?? ''))}
      live={activity || agent.handoff ? liveOf(agent) : ''}
      folded={collapsed.has(agent.name)}
      onFold={() => change({ collapsed: toggleIn(nav.collapsed, agent.name) })}
      unreached={unreached.get(agent.name) ?? null}
      selected={agent.name === selected}
      pinned={nav.pins.includes(agent.name)}
      onPin={() => togglePinAgent(agent.name)}
    />
  );
  const counts = new Map<Dot, number>();
  for (const a of agents) counts.set(dotOf(a), (counts.get(dotOf(a)) ?? 0) + 1);
  const myTurn = useMyTurn().cards.length;
  const hr = useHrCount();
  const onTeam = !!useTeamRoute();
  const onAccounts = useAccountsRoute().open;
  const pageRoute = usePageRoute();
  const onUsage = onAccounts || pageRoute === 'stats';
  return (
    <nav
      className="rail flex w-[var(--rail-w,280px)] flex-none flex-col overflow-hidden border-r border-line bg-panel min-h-0 max-[900px]:fixed max-[900px]:z-10 max-[900px]:[inset:0_auto_0_0] max-[900px]:w-[min(320px,90vw)] max-[900px]:shadow-[var(--shadow-2)]"
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
      </div>
      <div className="rail-opts relative flex items-center gap-1.5 px-2.5 py-2">
        <input
          className="search min-w-0 flex-1 rounded-ctl border border-line bg-canvas px-2 py-[5px]"
          type="search"
          placeholder="Search agents"
          aria-label="Search agents"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="relative inline-flex">
          <MenuButton icon="filter" label={nav.hidden.length ? `View: ${nav.hidden.length} hidden` : 'View'} name="View">
            <MenuLabel>Show agents that are</MenuLabel>
            {RAIL_FILTERS.map((f) => {
              const on = f.dots.every((d) => !hidden.has(d));
              const n = f.dots.reduce((t, d) => t + (counts.get(d) ?? 0), 0);
              return (
                <MenuCheckItem
                  key={f.id}
                  checked={on}
                  icon={<Mark dot={f.dots[0]!} title={MARKS[f.dots[0]!].label} />}
                  onCheckedChange={() => {
                    const g = f.id as NavGroup;
                    change({ hidden: on ? [...nav.hidden, g] : nav.hidden.filter((x) => x !== g) });
                  }}
                >
                  <span className="flex-1">{f.label}</span>
                  <span className="muted count">{n}</span>
                </MenuCheckItem>
              );
            })}
            <MenuSeparator />
            <MenuLabel>List</MenuLabel>
            <MenuCheckItem checked={tree} onCheckedChange={(on) => change({ layout: on ? 'tree' : 'flat' })}>
              Tree
            </MenuCheckItem>
            <MenuCheckItem checked={grouped} onCheckedChange={(on) => toggle('groupTeams', on, setGrouped)}>
              Group by team
            </MenuCheckItem>
            <MenuCheckItem checked={activity} onCheckedChange={(on) => toggle('activityLines', on, setActivity)}>
              Show what each agent is doing
            </MenuCheckItem>
          </MenuButton>
          {nav.hidden.length ? (
            <span
              aria-hidden
              data-testid="filter-active"
              className="pointer-events-none absolute right-1 top-1 size-2 rounded-full bg-focus"
            />
          ) : null}
        </span>
      </div>
      <div className="rail-nav flex flex-wrap gap-0.5 border-b border-line px-1.5 pb-1.5">
        <button
          className={navOf(pageRoute === 'my-turn')}
          aria-current={pageRoute === 'my-turn' ? 'page' : undefined}
          onClick={() => void (location.hash = '#/my-turn')}
        >
          <Icon name="inbox" size={14} aria-hidden /> Inbox{myTurn ? ` ${myTurn}` : ''}
        </button>
        <button className={navOf(onTasks)} aria-current={onTasks ? 'page' : undefined} onClick={() => goTasks()}>
          <Icon name="tasks" size={14} aria-hidden /> Tasks
        </button>
        <MoreMenu on={onTeam || onUsage} hint={hr}>
          <MenuItem icon="team" onSelect={() => goTeam(hr ? 'hr' : 'team')}>
            Team{hr ? ` · HR ${hr}` : ''}
          </MenuItem>
          <MenuItem icon="accounts" onSelect={() => void (location.hash = '#/accounts')}>
            Usage
          </MenuItem>
        </MoreMenu>
      </div>
      <div className="rail-read flex gap-0.5 px-2.5 py-1.5" role="group" aria-label="Show agents">
        {(
          [
            ['all', 'All'],
            ['unread', 'Unread'],
            ['needs', 'Needs me'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            className={`rounded-ctl border-0 px-2 py-0.5 text-label ${chip === id ? 'bg-canvas text-ink' : 'bg-transparent text-ink-2'}`}
            aria-pressed={chip === id}
            onClick={() => change(chipChanges(id))}
          >
            {label}
            {id !== 'all' && chipN[id] ? <span className="ml-1 text-wait">{chipN[id]}</span> : null}
          </button>
        ))}
      </div>
      <div className="rail-list min-h-0 flex-1 overflow-auto">
        {conn === 'open' ? null : <p className="muted pad">Connecting…</p>}
        <View q={list} served={served} shape="agents" what="the agent list" empty={conn === 'open' && !agents.length && 'No agents.'}>
          {nl.pinned.length ? (
            <>
              <h3 className="pinned-head m-0 px-2.5 pb-0.5 pt-2 text-hint font-normal uppercase tracking-wide text-ink-3">Pinned</h3>
              {nl.pinned.map((agent) => row(agent, 0, 0, teamOf(agent.name, names), `pin-${agent.name}`))}
            </>
          ) : null}
          {rows.map(({ agent, depth, kids }, i) => {
            const team = teamOf(agent.name, names);
            // A team's heading stands over its first top-level row; a child stays under the parent that spawned it.
            const head = heads && depth === 0 && team !== lastTop(rows, i, names);
            return (
              <Fragment key={agent.name}>
                {head ? (
                  <h3 className="team-head m-0 px-2.5 pb-0.5 pt-2 text-hint font-normal uppercase tracking-wide text-ink-3">
                    {team} <span className="count">{teamSize.get(team)}</span>
                  </h3>
                ) : null}
                {row(agent, depth, kids, team)}
              </Fragment>
            );
          })}
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

/** This device's old list settings move into the person's prefs once, then are cleared. */
function useMoveSettings(storage: KV | null, ready: boolean, names: string[], change: (c: NavPrefsChanges) => void) {
  useEffect(() => {
    if (!ready) return;
    const old = ['unreadOnly', 'hiddenDots', 'collapsed'].map((k) => setting(storage, k, '').value);
    if (!old.some(Boolean)) return;
    const [unread, dots, fold] = old as [string, string, string];
    const off = new Set(dots.split(',').filter(Boolean));
    const hidden = RAIL_FILTERS.filter((f) => f.dots.some((d) => off.has(d))).map((f) => f.id as NavGroup);
    change({ unread_only: unread === '1', hidden, collapsed: fold.split('|').filter((n) => names.includes(n)) });
    for (const k of ['unreadOnly', 'hiddenDots', 'collapsed']) setting(storage, k, '').save('');
  }, [storage, ready]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Grouped: each top-level row with the rows under it moves up beside the first of its team, so a heading stands once; the order within a team is kept. */
export function byTeam<R extends { agent: AgentView; depth: number }>(rows: R[], grouped: boolean, names: string[]): R[] {
  if (!grouped) return rows;
  const blocks = new Map<string, R[]>();
  let team = '';
  for (const r of rows) {
    if (r.depth === 0) team = teamOf(r.agent.name, names);
    blocks.set(team, [...(blocks.get(team) ?? []), r]);
  }
  return [...blocks.values()].flat();
}

/** The team of the last top-level row above row `i`, '' for the first. */
const lastTop = (rows: { agent: AgentView; depth: number }[], i: number, names: string[]): string => {
  for (let j = i - 1; j >= 0; j--) if (rows[j]!.depth === 0) return teamOf(rows[j]!.agent.name, names);
  return '';
};

/** The first line of the agent's newest request to the founder, for the line under its name. */
const lastLine = (a: AgentView): string => a.last_to_founder?.text.split('\n').find((l) => l.trim())?.trim() ?? '';

/** The nav row that opens the pages the top two leave out. */
function MoreMenu({ on, hint, children }: { on: boolean; hint: number; children: ReactNode }) {
  return (
    <MenuButton
      icon="more"
      label={hint ? `More (${hint} waiting)` : 'More'}
      aria-label={hint ? `More (${hint} waiting)` : 'More'}
      aria-current={on ? 'page' : undefined}
      trigger={hint ? `More ${hint}` : 'More'}
      name="More"
      align="start"
      className={`${navOf(on)} nav-more !justify-start`}
    >
      {children}
    </MenuButton>
  );
}
