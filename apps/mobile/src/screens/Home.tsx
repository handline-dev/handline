import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import type { Store } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { useUnread } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Accounts } from './Accounts.tsx';
import { AgentList } from './AgentList.tsx';
import { AgentPage } from './AgentPage.tsx';
import { Inbox } from './Inbox.tsx';
import { NewAgent, type Pending } from './NewAgent.tsx';
import { SettingsPage } from './Settings.tsx';
import { TeamPage } from './TeamEntry.tsx';
import { Stats } from './Stats.tsx';
import { Tasks } from './Tasks.tsx';
import { Worktrees } from './Worktrees.tsx';
import { Banner } from './parts.tsx';

const TABS = ['agents', 'inbox', 'tasks', 'usage'] as const;
type Tab = (typeof TABS)[number];

type Page = 'worktrees' | 'new' | 'settings' | 'team' | null;

/** `pending` is where New agent was left: with an operation in it, the app starts on that page. */
export function Home(props: { store: Store; onUnpair: () => void; pending?: Pending }) {
  const c = usePalette();
  const open = useSelector((s) => s.page?.agent ?? null);
  const conn = useSelector((s) => s.conn);
  const error = useSelector((s) => s.error);
  const unread = useUnread().length;
  const [tab, setTab] = useState<Tab>('agents');
  const [task, setTask] = useState<number | null>(null);
  const [account, setAccount] = useState<string | null>(null);
  const [page, setPage] = useState<Page>(props.pending?.operation ? 'new' : null);
  const [kept, setKept] = useState<string | null>(props.pending?.operation ?? null);
  const [first, setFirst] = useState(props.pending?.first ?? null);
  const pending: Pending = {
    operation: kept,
    first,
    save: (op, t = null) => (setKept(op), setFirst(t), props.pending?.save(op, t)),
  };
  const toAgent = (name: string) => (setPage(null), void props.store.openAgent(name));
  return (
    <View style={{ flex: 1 }}>
      {conn !== 'open' && <Banner text={conn === 'closed' ? 'Disconnected' : conn === 'connecting' ? 'Connecting…' : 'Reconnecting…'} />}
      {error && <Banner text={error} error onPress={() => props.store.dismissError()} />}
      <View style={{ flex: 1 }}>
        {open ? (
          <AgentPage />
        ) : page === 'worktrees' ? (
          <Worktrees onClose={() => setPage(null)} onAgent={toAgent} />
        ) : page === 'team' ? (
          <TeamPage onClose={() => setPage(null)} />
        ) : page === 'settings' ? (
          <SettingsPage onClose={() => setPage(null)} onUnpair={props.onUnpair} />
        ) : page === 'new' ? (
          <NewAgent pending={pending} onClose={() => setPage(null)} onAgent={toAgent} />
        ) : tab === 'inbox' ? (
          <Inbox />
        ) : tab === 'tasks' ? (
          <Tasks selected={task} onSelect={setTask} />
        ) : tab === 'usage' ? (
          <Accounts selected={account} onSelect={setAccount} footer={<Stats />} />
        ) : (
          <AgentList onPage={setPage} />
        )}
      </View>
      {!open && !page && (
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
              <Text numberOfLines={1} adjustsFontSizeToFit style={{ color: tab === t ? c.ink : c.muted, fontWeight: tab === t ? '700' : '400' }}>
                {t === 'agents'
                  ? 'Agents'
                  : t === 'inbox'
                    ? `Inbox${unread ? ` (${unread})` : ''}`
                    : t === 'tasks'
                      ? 'Tasks'
                      : 'Usage'}
              </Text>
            </Pressable>
          ))}
        </View>
      )}
    </View>
  );
}
