import { FlatList, Pressable, Text, View } from 'react-native';
import { useAgents, useServes } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { rowHeight, space } from '@handline/tokens';
import { dotColor, usePalette } from '../theme.ts';
import { Button } from './parts.tsx';
import { Loadable } from './Loadable.tsx';

export function AgentList(props: { onUnpair: () => void }) {
  const c = usePalette();
  const store = useStore();
  const query = useAgents();
  const { served } = useServes('agents.list');
  const agents = query.data?.agents ?? [];
  return (
    <View style={{ flex: 1 }}>
      <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700', padding: space.lg }}>Agents</Text>
      <Loadable query={query} served={served} isEmpty={agents.length === 0} skeleton="agents" label="agents" empty="No agents.">
        <FlatList
          data={agents}
          keyExtractor={(a) => a.name}
          renderItem={({ item: a }) => {
            const sub = [a.task?.title, a.account && `acct ${a.account}`, a.model].filter(Boolean).join(' · ');
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
                <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: dotColor(c, a.state) }} />
                <View style={{ flex: 1 }}>
                  <Text style={{ color: c.ink, fontWeight: '600' }}>{a.name}</Text>
                  <Text style={{ color: c.muted, fontSize: 12 }}>
                    {a.state}
                    {sub ? ` · ${sub}` : ''}
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
