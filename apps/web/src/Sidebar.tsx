// The agent list. A row shows an agent's state, its queue and a fold button when it has children; its actions (Start, the ⋯ menu)
// show while the pointer is on it or it is selected. One filter button opens the menu of what to show.
import { Fragment, useEffect, useState } from 'react';
import {
  turnAgents,
  chipList,
  dotOf,
  stuckUnder,
  workingUnder,
  hiddenDots,
  teamOf,
  togglePin as toggleIn,
  RAIL_FILTERS,
  agentLine,
  type Dot,
  type KV,
  type NavGroup,
} from '@handline/app-core';
import type { AgentView, NavPrefsChanges } from '@handline/client';
import { useConnection } from '@handline/app-react';
import { useAgents, useHrCount, useMyTurn, useNav, useServes } from '@handline/app-data';
import { useRowDrag } from '#src/RowDrag.ts';
import { Mark, MARKS } from '#src/RailIcons.tsx';
import { Button, Icon, MenuButton, MenuCheckItem, MenuLabel, MenuSeparator } from '#src/ui/index.ts';
import { setting } from '#src/daemon.ts';
import { goTasks, usePageRoute } from '#src/route.ts';
import { AgentRow } from '#src/AgentRow.tsx';
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
  const { nav, ready, change, togglePin: togglePinAgent } = useNav();
  const hidden = hiddenDots(nav.hidden);
  const collapsed = new Set(nav.collapsed);
  useMoveSettings(
    storage,
    ready && !!list.data,
    agents.map((x) => x.name),
    change,
  );
  // The view menu: the list under its teams.
  const [grouped, setGrouped] = useState(() => setting(storage, 'groupTeams', '1').value !== '0');
  const toggle = (key: string, on: boolean, set: (on: boolean) => void) => {
    setting(storage, key, '').save(on ? '1' : '0');
    set(on);
  };
  const turn = useMyTurn().cards;
  const names = agents.map((x) => x.name);
  const tree = nav.layout === 'tree';
  const nl = chipList(agents, { ...nav, unread_only: false }, turnAgents(turn));
  const rows = tree ? byTeam(nl.rows, grouped, names) : nl.rows;
  const heads = tree && grouped;
  // Beside an agent: the rows shown under the same parent (the top level within its team), in the order shown.
  const siblingsOf = (a: AgentView) =>
    rows
      .filter(
        (r) =>
          r.agent.parent === a.parent && (a.parent || r.depth === 0) && (a.parent || teamOf(r.agent.name, names) === teamOf(a.name, names)),
      )
      .map((r) => r.agent.name);
  const drag = useRowDrag(tree, nav.order, siblingsOf, (order) => change({ order }));
  const row = (agent: AgentView, depth: number, kids: number, team: string, key?: string) => {
    const pin = !!key?.startsWith('pin-'); // a pinned row always counts the work under it, its tree open or not
    return (
      <AgentRow
        key={key}
        agent={agent}
        depth={depth}
        kids={kids}
        working={pin || collapsed.has(agent.name) ? workingUnder(agents, agent.name) : 0}
        stuck={pin || collapsed.has(agent.name) ? stuckUnder(agents, agent.name) : 0}
        team={team}
        line={agentLine(agent)}
        folded={collapsed.has(agent.name)}
        onFold={() => change({ collapsed: toggleIn(nav.collapsed, agent.name) })}
        selected={agent.name === selected}
        pinned={nav.pins.includes(agent.name)}
        onPin={() => togglePinAgent(agent.name)}
        drag={pin ? undefined : { attrs: drag.attrs(agent), mark: drag.mark(agent.name) }}
      />
    );
  };
  const counts = new Map<Dot, number>();
  for (const a of agents) counts.set(dotOf(a), (counts.get(dotOf(a)) ?? 0) + 1);
  const myTurn = turn.length;
  const hr = useHrCount();
  const pageRoute = usePageRoute();
  return (
    <nav
      className="rail flex w-[var(--rail-w,280px)] flex-none flex-col overflow-hidden border-r border-line bg-panel min-h-0 max-[900px]:fixed max-[900px]:z-10 max-[900px]:[inset:0_auto_0_0] max-[900px]:w-[min(320px,90vw)] max-[900px]:shadow-[var(--shadow)]"
      aria-label="Agents"
    >
      {/* The shell's drag strip: row as tall as the top bar, centred on the traffic lights (--h-top and --lights-w follow the zoom: shell.ts); `rail-top` is the tests' hook. */}
      <div className="rail-top flex items-center gap-0.5 h-[var(--h-top)] px-2.5 shell:py-0 shell:pl-[var(--lights-w,86px)] shell:[-webkit-app-region:drag]">
        <Button
          className="shell:[-webkit-app-region:no-drag] shell:!h-[calc(var(--h-top)-4px)] shell:!w-[calc(var(--h-top)-4px)]"
          icon="sidebar"
          label="Hide the agent list (⌘B)"
          onClick={onToggle}
        />
        <Button
          className="shell:[-webkit-app-region:no-drag] shell:!h-[calc(var(--h-top)-4px)] shell:!w-[calc(var(--h-top)-4px)]"
          icon="back"
          label="Back (⌘[)"
          onClick={() => history.back()}
        />
        <Button
          className="shell:[-webkit-app-region:no-drag] shell:!h-[calc(var(--h-top)-4px)] shell:!w-[calc(var(--h-top)-4px)]"
          icon="forward"
          label="Forward (⌘])"
          onClick={() => history.forward()}
        />
      </div>
      <div className="rail-nav flex flex-wrap gap-0.5 border-b border-line px-1.5 pb-1.5">
        <button className={navOf(onTasks)} aria-current={onTasks ? 'page' : undefined} onClick={() => goTasks()}>
          <Icon name="tasks" size={14} aria-hidden /> Tasks
        </button>
        <button
          className={navOf(pageRoute === 'requests')}
          aria-current={pageRoute === 'requests' ? 'page' : undefined}
          onClick={() => void (location.hash = '#/requests')}
        >
          <Icon name="inbox" size={14} aria-hidden /> Requests
          {myTurn ? <span className="ml-auto rounded-full bg-wait px-1.5 text-xs font-semibold text-canvas">{myTurn}</span> : null}
        </button>
        <button
          className={navOf(pageRoute === 'stats')}
          aria-current={pageRoute === 'stats' ? 'page' : undefined}
          onClick={() => void (location.hash = '#/stats')}
        >
          <Icon name="stats" size={14} aria-hidden /> Usage
        </button>
      </div>
      <div className="rail-read flex gap-0.5 px-2.5 py-1.5" role="group" aria-label="Show agents">
        <span className="rail-opts relative ml-auto inline-flex">
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
          </MenuButton>
          {nav.hidden.length ? (
            <span
              aria-hidden
              data-testid="filter-active"
              className="pointer-events-none absolute right-1 top-1 size-2 rounded-full bg-acc"
            />
          ) : null}
        </span>
      </div>
      <div className="rail-list min-h-0 flex-1 overflow-auto">
        {conn === 'open' ? null : <p className="muted pad">Connecting…</p>}
        <View q={list} served={served} shape="agents" what="the agent list" empty={conn === 'open' && !agents.length && 'No agents.'}>
          {nl.pinned.length ? (
            <>
              <h3 className="pinned-head m-0 px-2.5 pb-0.5 pt-2 text-xs font-normal uppercase tracking-wide text-ink-3">Pinned</h3>
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
                  <h3 className="team-head m-0 px-2.5 pb-0.5 pt-2 text-xs font-normal uppercase tracking-wide text-ink-3">{team}</h3>
                ) : null}
                {row(agent, depth, kids, team)}
              </Fragment>
            );
          })}
        </View>
      </div>
      <NewAgentButton />
      <footer className="rail-foot flex gap-0.5 border-t border-line px-2 py-1">
        <span className="relative inline-flex">
          <Button icon="settings" label="Settings" onClick={() => void (location.hash = '#/settings')} />
          {hr ? (
            <span
              className="rail-hr pointer-events-none absolute right-0.5 top-0.5 size-2 rounded-full bg-bad"
              title={`HR proposals: ${hr} · needs you`}
            />
          ) : null}
        </span>
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
