import { useState } from 'react';
import { SectionList, Pressable, Text, View } from 'react-native';
import type { AgentView as Agent } from '@handline/client';
import { useAgents, useServes } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { rowHeight, space } from '@handline/tokens';
import { ago } from '@handline/app-core';
import { KIND } from './NeedsYouCard.tsx';
import { usePalette } from '../theme.ts';
import { StateMark } from './StateMark.tsx';
import { Button, MenuItem, MenuSheet, PageHeader } from './parts.tsx';
import { useHrCount } from './Hr.tsx';
import { TeamButton } from './TeamEntry.tsx';
import { Loadable } from './Loadable.tsx';

export function AgentList(props: { onPage: (page: 'worktrees' | 'new' | 'settings' | 'team') => void }) {
  const c = usePalette();
  const store = useStore();
  const query = useAgents();
  const { served } = useServes('agents.list');
  // Who needs the person comes first; the daemon's order otherwise.
  const agents = query.data?.agents ?? [];
  // An older daemon sets has_dialog without needs_you.
  const asks = (a: Agent) => !!a.needs_you || a.has_dialog;
  const groups = [
    { title: 'Needs you', data: agents.filter(asks) },
    { title: 'Working', data: agents.filter((a) => !asks(a) && a.state === 'working') },
    { title: 'Idle', data: agents.filter((a) => !asks(a) && a.state !== 'working') },
  ].filter((g) => g.data.length > 0);
  const founder = store.client.hello?.role === 'founder';
  const create = useServes('agent.create').served;
  const hr = useHrCount();
  const [menu, setMenu] = useState(false);
  const go = (page: 'worktrees' | 'settings' | 'team') => (setMenu(false), props.onPage(page));
  return (
    <View style={{ flex: 1 }}>
      <PageHeader
        title="Agents"
        actions={
          <>
            {founder && create ? (
              <Button testID="new-agent-open" label="+" accessibilityLabel="New agent" onPress={() => props.onPage('new')} style={{ paddingVertical: space.sm }} />
            ) : null}
            <Button
              testID="agents-menu"
              label={hr ? '⋯ ●' : '⋯'}
              accessibilityLabel="More"
              onPress={() => setMenu(!menu)}
              style={{ paddingVertical: space.sm }}
            />
          </>
        }
      />
      {menu && (
        <MenuSheet testID="agents-menu-list" onClose={() => setMenu(false)}>
          <TeamButton onPress={() => go('team')} />
          <MenuItem testID="worktrees-open" label="Worktrees" onPress={() => go('worktrees')} />
          <MenuItem testID="settings-open" label="Settings" onPress={() => go('settings')} />
        </MenuSheet>
      )}
      <Loadable query={query} served={served} isEmpty={agents.length === 0} skeleton="agents" label="agents" empty="No agents.">
        <SectionList
          sections={groups}
          keyExtractor={(a) => a.name}
          renderSectionHeader={({ section }) => (
            <Text style={{ color: c.muted, fontSize: 12, paddingHorizontal: space.lg, paddingTop: space.md, paddingBottom: space.xs }}>
              {section.title}
            </Text>
          )}
          renderItem={({ item: a }) => {
            const need = a.needs_you ? a.needs_you.text ?? KIND[a.needs_you.kind] : '';
            const doing = need || a.task?.title || a.state_detail || '';
            const word = asks(a) ? 'asks' : a.block ? a.block.kind : a.state;
            const age = a.needs_you ? a.needs_you.since : a.state_since;
            return (
              <Pressable
                testID={`agent-${a.name}`}
                onPress={() => void store.openAgent(a.name)}
                style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: space.md,
                  minHeight: rowHeight.agent, // the skeleton row's height; a larger font grows the row
                  paddingVertical: space.sm,
                  paddingHorizontal: space.lg,
                  borderTopWidth: 1,
                  borderTopColor: c.line,
                }}
              >
                <StateMark agent={a} />
                <View style={{ flex: 1 }}>
                  <Text style={{ color: c.ink, fontWeight: '600' }}>{a.name}</Text>
                  <Text numberOfLines={1} testID={need ? `needs-${a.name}` : undefined} style={{ color: need ? c.wait : c.muted, fontSize: 12 }}>
                    {doing || ' '}
                  </Text>
                </View>
                <View style={{ alignItems: 'flex-end' }}>
                  <Text style={{ color: need ? c.wait : a.block ? c.block : c.muted, fontSize: 12 }}>{word}</Text>
                  {age ? <Text style={{ color: c.muted, fontSize: 11 }}>{ago(age)}</Text> : null}
                </View>
              </Pressable>
            );
          }}
        />
      </Loadable>
    </View>
  );
}
