// The Team page's data for the PC and the phone: the team, who is doing what, the agents grouped under whom they report to, and which card is open.
import { useState } from 'react';
import type { AgentView, TeamAgent } from '@handline/client';
import { useServes } from './provider.ts';
import { useAgents, useTeam } from './read.ts';

/** The agents under one "reports to": the roots first, then each manager's reports, each group sorted by name. */
export function groups(agents: TeamAgent[]): { parent: string | null; agents: TeamAgent[] }[] {
  const by = new Map<string | null, TeamAgent[]>();
  for (const a of [...agents].sort((x, y) => x.name.localeCompare(y.name))) by.set(a.parent, [...(by.get(a.parent) ?? []), a]);
  return [...by.entries()]
    .sort(([x], [y]) => (x === null ? -1 : y === null ? 1 : x.localeCompare(y)))
    .map(([parent, list]) => ({ parent, agents: list }));
}

export function useTeamBoard() {
  const query = useTeam();
  const { served } = useServes('team.get');
  const agents = useAgents().data?.agents ?? [];
  const [open, setOpen] = useState<string | null>(null);
  const team = query.data;
  const doing = (name: string): AgentView | undefined => agents.find((a) => a.name === name);
  return {
    query,
    served,
    team,
    groups: groups(team?.agents ?? []),
    edges: team?.edges ?? [],
    doing,
    isOpen: (name: string) => open === name,
    toggle: (name: string) => setOpen(open === name ? null : name),
  };
}
