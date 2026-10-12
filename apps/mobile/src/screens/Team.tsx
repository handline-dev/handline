// The Team list: who is who and who may ask whom, as the web's TeamPage reads team.get. A card opens to the agents it may ask and be asked by.
import { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import type { AgentView, TeamAgent } from '@handline/client';
import { useAgents, useServes, useTeam } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';

/** The agents under one "reports to": the roots first, then each manager's reports, each group sorted by name. */
export function groups(agents: TeamAgent[]): { parent: string | null; agents: TeamAgent[] }[] {
  const by = new Map<string | null, TeamAgent[]>();
  for (const a of [...agents].sort((x, y) => x.name.localeCompare(y.name))) by.set(a.parent, [...(by.get(a.parent) ?? []), a]);
  return [...by.entries()]
    .sort(([x], [y]) => (x === null ? -1 : y === null ? 1 : x.localeCompare(y)))
    .map(([parent, list]) => ({ parent, agents: list }));
}

export function Team() {
  const c = usePalette();
  const query = useTeam();
  const { served } = useServes('team.get');
  const agents = useAgents().data?.agents ?? [];
  const [open, setOpen] = useState<string | null>(null);
  const team = query.data;
  const doing = (name: string): AgentView | undefined => agents.find((a) => a.name === name);
  return (
    <Loadable
      query={query}
      served={served}
      isEmpty={!!team && team.agents.length === 0}
      skeleton="team"
      label="the team"
      empty="No agents in the team."
    >
      <ScrollView testID="team-list" contentContainerStyle={{ padding: space.lg, gap: space.md }}>
        {groups(team?.agents ?? []).map((g) => (
          <View key={g.parent ?? ''} style={{ gap: space.sm }}>
            <Text style={{ color: c.muted, fontWeight: '600' }}>{g.parent ? `Reports to ${g.parent}` : 'Top of the team'}</Text>
            {g.agents.map((a) => (
              <Card
                key={a.name}
                a={a}
                d={doing(a.name)}
                edges={team?.edges ?? []}
                open={open === a.name}
                onPress={() => setOpen(open === a.name ? null : a.name)}
              />
            ))}
          </View>
        ))}
      </ScrollView>
    </Loadable>
  );
}

function Card(props: {
  a: TeamAgent;
  d: AgentView | undefined;
  edges: { from: string; to: string }[];
  open: boolean;
  onPress: () => void;
}) {
  const c = usePalette();
  const { a, d } = props;
  const names = (list: string[]) => (list.length ? list.join(', ') : 'nobody');
  return (
    <Pressable
      testID={`team-${a.name}`}
      accessibilityRole="button"
      accessibilityState={{ expanded: props.open }}
      onPress={props.onPress}
      style={{ gap: space.xs, padding: space.md, borderWidth: 1, borderColor: c.line, borderRadius: radius.md }}
    >
      <Text style={{ color: c.ink, fontWeight: '700' }}>{a.name}</Text>
      {d && (
        <Text style={{ color: c.muted, fontSize: 12 }}>
          {[
            d.block ? `blocked (${d.block.kind})` : d.state,
            d.state_detail,
            d.account,
            d.model && `${d.model}${d.effort ? ` · ${d.effort}` : ''}`,
          ]
            .filter(Boolean)
            .join(' · ')}
        </Text>
      )}
      <Text style={{ color: c.ink }}>{a.role}</Text>
      {d?.task && (
        <Text style={{ color: c.muted, fontSize: 12 }}>
          doing T{d.task.id} {d.task.title}
        </Text>
      )}
      {props.open && (
        <View testID={`team-edges-${a.name}`} style={{ gap: space.xs, paddingTop: space.xs }}>
          <Text style={{ color: c.ink }}>
            <Text style={{ color: c.muted }}>may ask </Text>
            {names(props.edges.filter((e) => e.from === a.name).map((e) => e.to))}
          </Text>
          <Text style={{ color: c.ink }}>
            <Text style={{ color: c.muted }}>may be asked by </Text>
            {names(props.edges.filter((e) => e.to === a.name).map((e) => e.from))}
          </Text>
        </View>
      )}
    </Pressable>
  );
}
