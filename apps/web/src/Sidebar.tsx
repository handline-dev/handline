// The agent list. A row shows an agent's state, its queue and a fold button when it has children; its actions (Start, the ⋯ menu)
// show while the pointer is on it or it is selected. One filter button opens the menu of what to show.
import { Fragment, useState, type ReactNode } from 'react';
import { dotOf, railRows, teamOf, visibleRows, RAIL_FILTERS, type Dot, type KV, taskRef } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useConnection } from '@handline/app-react';
import { useAgents, useUnreached, useServes, useUnread, useWorktrees } from '@handline/app-data';
import { useMyTurn } from './MyTurn.tsx';
import { useHrCount } from './TeamPage.tsx';
import { Mark, MARKS } from './RailIcons.tsx';
import { Button, Icon, MenuButton, MenuCheckItem, MenuItem, MenuSeparator } from './ui/index.ts';
import { setting } from './daemon.ts';
import {
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
import { AgentRow, liveOf } from './AgentRow.tsx';
import { NewAgentButton } from './NewAgent.tsx';
import { View } from './View.tsx';
import { navOf, usePairPhone } from './NavRows.tsx';

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
  const rows = visibleRows(railRows(agents, { hidden, search }), collapsed);
  const names = agents.map((x) => x.name);
  const teamSize = new Map<string, number>();
  for (const a of agents) teamSize.set(teamOf(a.name, names), (teamSize.get(teamOf(a.name, names)) ?? 0) + 1);
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
  const pair = usePairPhone();
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
      <div className="rail-opts relative flex items-center gap-1.5 px-2.5 py-2">
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
          label={hidden.size ? `View: ${hidden.size} hidden` : 'View'}
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
          <MenuSeparator />
          <MenuCheckItem checked={grouped} onCheckedChange={(on) => toggle('groupTeams', on, setGrouped)}>
            Group by team
          </MenuCheckItem>
          <MenuCheckItem checked={activity} onCheckedChange={(on) => toggle('activityLines', on, setActivity)}>
            Show what each agent is doing
          </MenuCheckItem>
        </MenuButton>
      </div>
      <div className="rail-nav flex flex-wrap gap-0.5 border-b border-line px-1.5 pb-1.5">
        <button
          className={navOf(onPage === 'my-turn')}
          aria-current={onPage === 'my-turn' ? 'page' : undefined}
          onClick={() => void (location.hash = '#/my-turn')}
        >
          <Icon name="decision" size={14} aria-hidden /> My turn{myTurn ? ` ${myTurn}` : ''}
        </button>
        <button className={navOf(onTasks)} aria-current={onTasks ? 'page' : undefined} onClick={() => goTasks()}>
          <Icon name="tasks" size={14} aria-hidden /> Tasks
        </button>
        <MoreMenu on={onInbox || onTeam || onWorktrees || onPage === 'accounts' || onPage === 'stats'} hint={unread + hr}>
          <MenuItem icon="inbox" onSelect={goInbox}>
            Inbox{unread ? ` ${unread}` : ''}
          </MenuItem>
          <MenuItem icon="team" onSelect={() => goTeam(hr ? 'hr' : 'team')}>
            Team{hr ? ` · HR ${hr}` : ''}
          </MenuItem>
          {worktreesServed ? (
            <MenuItem icon="worktrees" onSelect={() => goWorktrees()}>
              Worktrees
            </MenuItem>
          ) : null}
          <MenuItem icon="accounts" onSelect={() => void (location.hash = '#/accounts')}>
            Accounts
          </MenuItem>
          <MenuItem icon="stats" onSelect={() => void (location.hash = '#/stats')}>
            Stats
          </MenuItem>
          {pair.can ? (
            <>
              <MenuSeparator />
              <MenuItem icon="open" onSelect={pair.run}>
                Pair a phone
              </MenuItem>
            </>
          ) : null}
        </MoreMenu>
        {pair.error && (
          <p className="m-0 basis-full px-2 text-hint text-bad" role="alert">
            {pair.error}
          </p>
        )}
      </div>
      <div className="rail-list min-h-0 flex-1 overflow-auto">
        {conn === 'open' ? null : <p className="muted pad">Connecting…</p>}
        <View q={list} served={served} shape="agents" what="the agent list" empty={conn === 'open' && !agents.length && 'No agents.'}>
          {rows.map(({ agent, depth, kids }, i) => {
            const team = teamOf(agent.name, names);
            // A team's heading stands over its first top-level row; a child stays under the parent that spawned it.
            const head = grouped && depth === 0 && team !== lastTop(rows, i, names);
            return (
              <Fragment key={agent.name}>
                {head ? (
                  <h3 className="team-head m-0 px-2.5 pb-0.5 pt-2 text-hint font-normal uppercase tracking-wide text-ink-3">
                    {team} <span className="count">{teamSize.get(team)}</span>
                  </h3>
                ) : null}
                <AgentRow
                  agent={agent}
                  depth={depth}
                  kids={kids}
                  team={team}
                  sub={agent.task ? `${taskRef(agent.task.id)} ${agent.task.title}` : (where.get(agent.name) ?? '')}
                  live={activity ? liveOf(agent) : ''}
                  folded={collapsed.has(agent.name)}
                  onFold={() => setCollapsed(flip(collapsed, agent.name, 'collapsed', '|'))}
                  unreached={unreached.get(agent.name) ?? null}
                  selected={agent.name === selected}
                />
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

/** The team of the last top-level row above row `i`, '' for the first. */
const lastTop = (rows: { agent: AgentView; depth: number }[], i: number, names: string[]): string => {
  for (let j = i - 1; j >= 0; j--) if (rows[j]!.depth === 0) return teamOf(rows[j]!.agent.name, names);
  return '';
};

/** The nav row that opens the pages the top two leave out. */
function MoreMenu({ on, hint, children }: { on: boolean; hint: number; children: ReactNode }) {
  return (
    <MenuButton
      icon="more"
      label={hint ? `More (${hint} waiting)` : 'More'}
      aria-label={hint ? `More (${hint} waiting)` : 'More'}
      trigger={hint ? `More ${hint}` : 'More'}
      name="More"
      align="start"
      className={`${navOf(on)} nav-more !justify-start`}
    >
      {children}
    </MenuButton>
  );
}
