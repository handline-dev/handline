import { FlatList, Pressable, Text, View } from 'react-native';
import { useAgents, useStore } from '@handline/app-react';
import { space } from '@handline/tokens';
import { dotColor, usePalette } from '../theme.ts';
import { Button } from './parts.tsx';

export function AgentList(props: { onUnpair: () => void }) {
  const c = usePalette();
  const store = useStore();
  const agents = useAgents();
  return (
    <View style={{ flex: 1 }}>
      <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700', padding: space.lg }}>Agents</Text>
      <FlatList
        data={agents}
        keyExtractor={(a) => a.name}
        ListEmptyComponent={<Text style={{ color: c.muted, padding: space.lg }}>No agents.</Text>}
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
                padding: space.lg,
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
      <Button label="Unpair this phone" onPress={props.onUnpair} style={{ margin: space.lg }} />
    </View>
  );
}
