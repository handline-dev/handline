import { FlatList, Pressable, Text, View } from 'react-native';
import { useAgents, useServes } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { rowHeight, space } from '@handline/tokens';
import { ago } from '@handline/app-core';
import { KIND } from './NeedsYouCard.tsx';
import { usePalette } from '../theme.ts';
import { StateMark } from './StateMark.tsx';
import { Button } from './parts.tsx';
import { Loadable } from './Loadable.tsx';

export function AgentList(props: { onUnpair: () => void; onPage: (page: 'worktrees' | 'new') => void }) {
  const c = usePalette();
  const store = useStore();
  const query = useAgents();
  const { served } = useServes('agents.list');
  // Who needs the person comes first; the daemon's order otherwise.
  const agents = [...(query.data?.agents ?? [])].sort((a, b) => Number(!!b.needs_you) - Number(!!a.needs_you));
  const founder = store.client.hello?.role === 'founder';
  const create = useServes('agent.create').served;
  return (
    <View style={{ flex: 1 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: space.lg }}>
        <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700' }}>Agents</Text>
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          <Button
            testID="worktrees-open"
            label="Worktrees"
            onPress={() => props.onPage('worktrees')}
            style={{ paddingVertical: space.sm }}
          />
          {founder && create ? (
            <Button testID="new-agent-open" label="New agent" onPress={() => props.onPage('new')} style={{ paddingVertical: space.sm }} />
          ) : null}
        </View>
      </View>
      <Loadable query={query} served={served} isEmpty={agents.length === 0} skeleton="agents" label="agents" empty="No agents.">
        <FlatList
          data={agents}
          keyExtractor={(a) => a.name}
          renderItem={({ item: a }) => {
            const sub = [a.task?.title, a.account && `acct ${a.account}`, a.model].filter(Boolean).join(' · ');
            const need = a.needs_you ? `${a.needs_you.text ?? KIND[a.needs_you.kind]} · ${ago(a.needs_you.since)}` : '';
            return (
              <Pressable
                testID={`agent-${a.name}`}
                onPress={() => void store.openAgent(a.name)}
                style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: space.md,
                  height: rowHeight.agent, // the skeleton row is exactly this tall
                  paddingHorizontal: space.lg,
                  borderTopWidth: 1,
                  borderTopColor: c.line,
                }}
              >
                <StateMark agent={a} />
                <View style={{ flex: 1 }}>
                  <Text style={{ color: c.ink, fontWeight: '600' }}>{a.name}</Text>
                  <Text testID={need ? `needs-${a.name}` : undefined} style={{ color: need ? c.wait : c.muted, fontSize: 12 }}>
                    {need || `${a.state}${sub ? ` · ${sub}` : ''}`}
                  </Text>
                </View>
                {a.has_dialog ? (
                  <Text style={{ color: c.wait }}>asks</Text>
                ) : a.block ? (
                  <Text style={{ color: c.block }}>{a.block.kind}</Text>
                ) : null}
              </Pressable>
            );
          }}
        />
      </Loadable>
      <Button label="Unpair this phone" onPress={props.onUnpair} style={{ margin: space.lg }} />
    </View>
  );
}
