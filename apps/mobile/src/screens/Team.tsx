// The Team list: who is who and who may ask whom, as the web's TeamPage reads team.get. A card opens to the agents it may ask and be asked by.
import { queueOf, taskRef } from '@handline/app-core';
import { Pressable, ScrollView, Text, View } from 'react-native';
import type { AgentView, TeamAgent } from '@handline/client';
import { useTeamBoard } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';
import { TeamSeal } from '#src/screens/TeamSeal.tsx';

export function Team() {
  const c = usePalette();
  const { query, served, team, groups, edges, doing, isOpen, toggle } = useTeamBoard();
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
        <TeamSeal />
        {groups.map((g) => (
          <View key={g.parent ?? ''} style={{ gap: space.sm }}>
            <Text style={{ color: c.muted, fontWeight: '600' }}>{g.parent ? `Reports to ${g.parent}` : 'Top of the team'}</Text>
            {g.agents.map((a) => (
              <Card
                key={a.name}
                a={a}
                d={doing(a.name)}
                edges={edges}
                open={isOpen(a.name)}
                onPress={() => toggle(a.name)}
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
  const queued = d ? queueOf(d).tasks : null;
  const names = (list: string[]) => (list.length ? list.join(', ') : 'nobody');
  return (
    <Pressable
      testID={`team-${a.name}`}
      accessibilityRole="button"
      accessibilityState={{ expanded: props.open }}
      onPress={props.onPress}
      style={{ gap: space.xs, padding: space.md, borderWidth: 1, borderColor: c.line, borderRadius: radius.m }}
    >
      <Text style={{ color: c.ink, fontWeight: '700' }}>{a.name}</Text>
      {d && (
        <Text style={{ color: c.muted, fontSize: font.size.sm }}>
          {[
            d.status?.text ?? (d.block ? `blocked (${d.block.kind})` : d.state),
            d.status ? null : d.state_detail,
            d.account,
            d.model && `${d.model}${d.effort ? ` · ${d.effort}` : ''}`,
          ]
            .filter(Boolean)
            .join(' · ')}
        </Text>
      )}
      <Text style={{ color: c.ink }}>{a.role}</Text>
      {d?.task && (
        <Text style={{ color: c.muted, fontSize: font.size.sm }}>
          doing {taskRef(d.task.id)} {d.task.title}
        </Text>
      )}
      {queued ? (
        <Text testID={`team-queue-${a.name}`} style={{ color: c.wait, fontSize: font.size.sm }}>
          {queued} {queued === 1 ? 'task' : 'tasks'} waiting
        </Text>
      ) : null}
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
