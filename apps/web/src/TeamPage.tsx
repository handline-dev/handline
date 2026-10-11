// The Team page: two tabs, Team (who is who, and who may ask whom) and HR (the proposals that wait for the founder).
import { useState } from 'react';
import { waitingCount } from '@handline/app-core';
import type { AgentView, TeamAgent } from '@handline/client';
import { useAgents, useHr, useServes, useTeam } from '@handline/app-data';
import { chatTab } from './chatTab.ts';
import { HrTab } from './HrTab.tsx';
import { Empty, Icon, State, stateOfView } from './ui/index.ts';
import { goAgent, goTeam, useTeamRoute } from './route.ts';
import { View } from './View.tsx';

/** How many proposals wait on the founder; 0 while the list is not here or not served. */
export const useHrCount = (): number => waitingCount(useHr().data?.proposals ?? []);

export function TeamPage() {
  const route = useTeamRoute()!;
  const { served: hrServed } = useServes('hr.list');
  const count = useHrCount();
  return (
    <section className="team" aria-label="Team">
      <div className="chat-tabs flex gap-1 border-b border-line px-3 pt-1.5" role="tablist" aria-label="Team pages">
        <button
          role="tab"
          aria-selected={route.tab === 'team'}
          className={chatTab(route.tab === 'team')}
          onClick={() => goTeam('team')}
        >
          Team
        </button>
        {hrServed ? (
          <button
            role="tab"
            aria-selected={route.tab === 'hr'}
            className={chatTab(route.tab === 'hr')}
            onClick={() => goTeam('hr')}
          >
            {count ? (
              <span className="hr-dot" aria-hidden="true">
                ●{' '}
              </span>
            ) : null}
            HR{count ? ` ${count}` : ''}
          </button>
        ) : null}
      </div>
      {route.tab === 'hr' && hrServed ? <HrTab focus={route.proposal} /> : <TeamTable />}
    </section>
  );
}

/** The agents under one "reports to": the roots first, then each manager's reports, each group sorted by name. */
export function groups(agents: TeamAgent[]): { parent: string | null; agents: TeamAgent[] }[] {
  const by = new Map<string | null, TeamAgent[]>();
  for (const a of [...agents].sort((x, y) => x.name.localeCompare(y.name))) by.set(a.parent, [...(by.get(a.parent) ?? []), a]);
  return [...by.entries()]
    .sort(([x], [y]) => (x === null ? -1 : y === null ? 1 : x.localeCompare(y)))
    .map(([parent, list]) => ({ parent, agents: list }));
}

function TeamTable() {
  const q = useTeam();
  const { served } = useServes('team.get');
  const agents = useAgents().data?.agents ?? [];
  const [open, setOpen] = useState<string | null>(null);
  const team = q.data;
  const doing = (name: string): AgentView | undefined => agents.find((a) => a.name === name);
  return (
    <View q={q} served={served} shape="team" what="the team" empty={false}>
      {team && !team.agents.length ? (
        <Empty icon="team" name="No agents in the team" />
      ) : (
        <div className="team-board">
          {groups(team?.agents ?? []).map((g) => (
            <section key={g.parent ?? ''} className="team-group" aria-label={g.parent ? `Reports to ${g.parent}` : 'Top of the team'}>
              {g.parent ? (
                <h3 title="Reports to">
                  <Icon name="agent" label="Reports to" size={14} aria-hidden="true" /> {g.parent}
                </h3>
              ) : null}
              <div className="team-cards">
                {g.agents.map((a) => (
                  <Card
                    key={a.name}
                    a={a}
                    d={doing(a.name)}
                    edges={team?.edges ?? []}
                    open={open === a.name}
                    onOpen={() => setOpen(open === a.name ? null : a.name)}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </View>
  );
}

const day = (t: string | null) => (t ? new Date(t).toLocaleDateString() : 'not set');

function Card(props: { a: TeamAgent; d: AgentView | undefined; edges: { from: string; to: string }[]; open: boolean; onOpen: () => void }) {
  const { a, d } = props;
  const chips = (names: string[]) =>
    names.length ? (
      names.map((n) => (
        <button key={n} className="chip btn" onClick={() => goAgent(n)}>
          {n}
        </button>
      ))
    ) : (
      <span className="muted">nobody</span>
    );
  return (
    <article
      className={`team-card${props.open ? ' open' : ''}`}
      data-agent={a.name}
      onClick={props.onOpen}
      title={`role file ${day(a.role_file.updated)} · base file ${day(a.base_file.updated)}`}
    >
      <header>
        {d ? (
          <State
            kind={stateOfView(d)}
            label={[d.block ? `blocked (${d.block.kind})` : d.state, d.state_detail].filter(Boolean).join(' · ')}
          />
        ) : null}
        <button className="team-name" onClick={(e) => (e.stopPropagation(), goAgent(a.name))}>
          {a.name}
        </button>
        <button
          className="team-more"
          aria-expanded={props.open}
          aria-label={`Who ${a.name} may ask`}
          title="Who may ask whom"
          onClick={(e) => (e.stopPropagation(), props.onOpen())}
        >
          <Icon name={props.open ? 'less' : 'more'} label="Who may ask whom" size={14} aria-hidden="true" />
        </button>
      </header>
      <p className="team-role">{a.role}</p>
      {d?.account || d?.model ? (
        <p className="team-meta">
          {d.account ? (
            <span title="Account">
              <Icon name="accounts" label="Account" size={14} aria-hidden="true" /> {d.account}
            </span>
          ) : null}
          {d.model ? (
            <span title="Model">
              <Icon name="model" label="Model" size={14} aria-hidden="true" /> {d.model}
              {d.effort ? ` · ${d.effort}` : ''}
            </span>
          ) : null}
        </p>
      ) : null}
      {d?.task ? (
        <p className="team-task" title="Doing">
          <Icon name="tasks" label="Doing" size={14} aria-hidden="true" /> #{d.task.id} {d.task.title}
        </p>
      ) : null}
      {props.open ? (
        <div className="edges">
          <div>
            <Icon name="send" label="May ask" size={14} />
            {chips(props.edges.filter((e) => e.from === a.name).map((e) => e.to))}
          </div>
          <div>
            <Icon name="reply" label="May be asked by" size={14} />
            {chips(props.edges.filter((e) => e.to === a.name).map((e) => e.from))}
          </div>
        </div>
      ) : null}
    </article>
  );
}
