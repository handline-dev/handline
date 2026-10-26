// The Team page's data for the PC and the phone: the team, who is doing what, the agents grouped under whom they report to, and which card is open.
import { useState } from 'react';
import type { AgentView, TeamAgent } from '@handline/client';
import { useServes } from '#src/provider.ts';
import { useAgents, useTeam } from '#src/read.ts';

/** The agents under one "reports to": the roots first, then each manager's reports, each group sorted by name. */
export function groups(agents: TeamAgent[]): { parent: string | null; agents: TeamAgent[] }[] {
  const by = new Map<string | null, TeamAgent[]>();
  for (const a of [...agents].sort((x, y) => x.name.localeCompare(y.name))) by.set(a.parent, [...(by.get(a.parent) ?? []), a]);
  return [...by.entries()]
    .sort(([x], [y]) => (x === null ? -1 : y === null ? 1 : x.localeCompare(y)))
    .map(([parent, list]) => ({ parent, agents: list }));
}

/** An agent and the agents that report to it, each level sorted by name; an agent whose manager is not on the team is a root. */
export interface TeamNode {
  agent: TeamAgent;
  children: TeamNode[];
}

export function teamTree(agents: TeamAgent[]): TeamNode[] {
  const names = new Set(agents.map((a) => a.name));
  const sorted = [...agents].sort((x, y) => x.name.localeCompare(y.name));
  const build = (a: TeamAgent): TeamNode => ({ agent: a, children: sorted.filter((c) => c.parent === a.name).map(build) });
  return sorted.filter((a) => a.parent === null || !names.has(a.parent)).map(build);
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
    tree: teamTree(team?.agents ?? []),
    edges: team?.edges ?? [],
    doing,
    isOpen: (name: string) => open === name,
    toggle: (name: string) => setOpen(open === name ? null : name),
  };
}
