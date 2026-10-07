// The Team page: two tabs, Team (who is who, and who may ask whom) and HR (the proposals that wait for the founder).
import { useState } from 'react';
import { dotOf, waitingCount } from '@handline/app-core';
import type { AgentView, TeamAgent } from '@handline/client';
import { useAgents, useHr, useServes, useTeam } from '@handline/app-data';
import { HrTab } from './HrTab.tsx';
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
      <div className="chat-tabs" role="tablist" aria-label="Team pages">
        <button
          role="tab"
          aria-selected={route.tab === 'team'}
          className={`chat-tab${route.tab === 'team' ? ' on' : ''}`}
          onClick={() => goTeam('team')}
        >
          Team
        </button>
        {hrServed ? (
          <button
            role="tab"
            aria-selected={route.tab === 'hr'}
            className={`chat-tab${route.tab === 'hr' ? ' on' : ''}`}
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

type SortKey = 'name' | 'role' | 'parent';

function TeamTable() {
  const q = useTeam();
  const { served } = useServes('team.get');
  const agents = useAgents().data?.agents ?? [];
  const [sort, setSort] = useState<SortKey>('name');
  const [open, setOpen] = useState<string | null>(null);
  const team = q.data;
  const rows = [...(team?.agents ?? [])].sort((a, b) => String(a[sort] ?? '').localeCompare(String(b[sort] ?? '')));
  const doing = (name: string): AgentView | undefined => agents.find((a) => a.name === name);
  const when = (t: string | null) => (t ? new Date(t).toLocaleDateString() : null);
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
    <View q={q} served={served} shape="team" what="the team" empty={team && !team.agents.length ? 'No agents in the team.' : false}>
      <table className="team-table">
        <thead>
          <tr>
            {(['name', 'role', 'parent'] as const).map((k) => (
              <th key={k}>
                <button className="btn" onClick={() => setSort(k)} aria-sort={sort === k ? 'ascending' : 'none'}>
                  {k === 'name' ? 'agent' : k === 'parent' ? 'reports to' : k}
                  {sort === k ? ' ▲' : ''}
                </button>
              </th>
            ))}
            <th>doing</th>
            <th>role file updated</th>
            <th>base file updated</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a: TeamAgent) => {
            const d = doing(a.name);
            return (
              <FragmentRow key={a.name}>
                <tr data-agent={a.name} onClick={() => setOpen(open === a.name ? null : a.name)}>
                  <td>
                    <button className="btn" onClick={(e) => (e.stopPropagation(), goAgent(a.name))}>
                      {a.name}
                    </button>
                  </td>
                  <td>{a.role}</td>
                  <td>{a.parent ?? ''}</td>
                  <td>
                    {d ? (
                      <>
                        <span className={`dot ${dotOf(d)}`} /> {d.state}
                        {d.task ? ` · #${d.task.id} ${d.task.title}` : ''}
                      </>
                    ) : null}
                  </td>
                  <td>{when(a.role_file.updated) ?? <span className="muted">not set</span>}</td>
                  <td>{when(a.base_file.updated) ?? <span className="muted">not set</span>}</td>
                </tr>
                {open === a.name ? (
                  <tr className="edges">
                    <td colSpan={6}>
                      <div>
                        <span className="muted">may ask </span>
                        {chips((team?.edges ?? []).filter((e) => e.from === a.name).map((e) => e.to))}
                      </div>
                      <div>
                        <span className="muted">may be asked by </span>
                        {chips((team?.edges ?? []).filter((e) => e.to === a.name).map((e) => e.from))}
                      </div>
                    </td>
                  </tr>
                ) : null}
              </FragmentRow>
            );
          })}
        </tbody>
      </table>
    </View>
  );
}

const FragmentRow = ({ children }: { children: React.ReactNode }) => <>{children}</>;
