import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import type { Store } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { useUnread } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { AgentList } from './AgentList.tsx';
import { AgentPage } from './AgentPage.tsx';
import { Inbox } from './Inbox.tsx';
import { Tasks } from './Tasks.tsx';
import { Banner } from './parts.tsx';

const TABS = ['agents', 'inbox', 'tasks'] as const;
type Tab = (typeof TABS)[number];

export function Home(props: { store: Store; onUnpair: () => void }) {
  const c = usePalette();
  const open = useSelector((s) => s.page?.agent ?? null);
  const conn = useSelector((s) => s.conn);
  const error = useSelector((s) => s.error);
  const unread = useUnread().length;
  const [tab, setTab] = useState<Tab>('agents');
  const [task, setTask] = useState<number | null>(null);
  return (
    <View style={{ flex: 1 }}>
      {conn !== 'open' && <Banner text={conn === 'closed' ? 'Disconnected' : conn === 'connecting' ? 'Connecting…' : 'Reconnecting…'} />}
      {error && <Banner text={error} error onPress={() => props.store.dismissError()} />}
      <View style={{ flex: 1 }}>
        {open ? (
          <AgentPage />
        ) : tab === 'inbox' ? (
          <Inbox />
        ) : tab === 'tasks' ? (
          <Tasks selected={task} onSelect={setTask} />
        ) : (
          <AgentList onUnpair={props.onUnpair} />
        )}
      </View>
      {!open && (
        <View
          accessibilityRole="tablist"
          style={{ flexDirection: 'row', borderTopWidth: 1, borderTopColor: c.line, backgroundColor: c.panel }}
        >
          {TABS.map((t) => (
            <Pressable
              key={t}
              testID={`nav-${t}`}
              accessibilityRole="tab"
              accessibilityState={{ selected: tab === t }}
              onPress={() => setTab(t)}
              style={{
                flex: 1,
                alignItems: 'center',
                padding: space.md,
                borderTopWidth: 2,
                borderTopColor: tab === t ? c.ink : 'transparent',
              }}
            >
              <Text style={{ color: tab === t ? c.ink : c.muted, fontWeight: tab === t ? '700' : '400' }}>
                {t === 'agents' ? 'Agents' : t === 'inbox' ? `Inbox${unread ? ` (${unread})` : ''}` : 'Tasks'}
              </Text>
            </Pressable>
          ))}
        </View>
      )}
    </View>
  );
}
