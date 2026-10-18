import { useEffect, useMemo, useRef, useState } from 'react';
import { BackHandler, Dimensions, PanResponder, Platform, Pressable, Text, View } from 'react-native';
import type { Store } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { useUnread } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { History, HistoryProvider, useHistoryValue } from '../history.tsx';
import { Accounts } from './Accounts.tsx';
import { AgentList } from './AgentList.tsx';
import { AgentPage } from './AgentPage.tsx';
import { Inbox } from './Inbox.tsx';
import { NewAgent, type Pending } from './NewAgent.tsx';
import { Stats } from './Stats.tsx';
import { Tasks } from './Tasks.tsx';
import { Worktrees } from './Worktrees.tsx';
import { Banner } from './parts.tsx';

const TABS = ['agents', 'inbox', 'tasks', 'accounts', 'stats'] as const;
type Tab = (typeof TABS)[number];

type Page = 'worktrees' | 'new' | null;

/** `pending` is where New agent was left: with an operation in it, the app starts on that page. */
export function Home(props: { store: Store; onUnpair: () => void; pending?: Pending }) {
  const history = useMemo(() => {
    const h = new History();
    const p = props.store.page;
    h.seed('agent', p ? `${p.agent}|${p.tab}` : null);
    return h;
  }, [props.store]);
  return (
    <HistoryProvider history={history}>
      <HomeScreen {...props} history={history} />
    </HistoryProvider>
  );
}

const edge = (x: number) => x < 24 || x > Dimensions.get('window').width - 24;

/** What back does at the first entry: nothing, so the phone's own back leaves the app. */
function useBack(history: History) {
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => history.back());
    return () => sub.remove();
  }, [history]);
  // iOS has no back button: a swipe in from the left edge goes back, from the right edge forward.
  return useMemo(
    () =>
      Platform.OS === 'ios'
        ? PanResponder.create({
            onMoveShouldSetPanResponder: (e, g) => Math.abs(g.dx) > 20 && Math.abs(g.dy) < 30 && (edge(e.nativeEvent.pageX - g.dx)),
            onPanResponderRelease: (_e, g) => void (Math.abs(g.dx) > 60 && (g.dx > 0 ? history.back() : history.forward())),
          }).panHandlers
        : {},
    [history],
  );
}

/** The agent page is the store's, not a history value: each side follows the other, and the history keeps the entry. */
function useAgentEntry(store: Store) {
  const open = useSelector((s) => (s.page ? `${s.page.agent}|${s.page.tab}` : null));
  const [entry, setEntry] = useHistoryValue<string | null>('agent', null);
  const seen = useRef({ store: open, entry });
  const now = useRef(open); // the store's key now, for when a history-driven open ends
  now.current = open;
  const driving = useRef(false); // a history-driven open is in flight: the store passes through other pages on the way
  useEffect(() => {
    if (driving.current) return;
    if (open !== seen.current.store) {
      seen.current = { store: open, entry: open };
      setEntry(open);
    } else if (entry !== seen.current.entry) {
      seen.current = { store: entry, entry };
      const [agent, tab] = entry?.split('|') ?? [];
      if (!agent) void store.closeAgent();
      else if (agent !== open?.split('|')[0]) {
        driving.current = true;
        void store
          .openAgent(agent)
          .then(() => store.setTab(tab as 'chat' | 'terminal'))
          .finally(() => ((driving.current = false), (seen.current.store = now.current)));
      } else store.setTab(tab as 'chat' | 'terminal');
    }
  });
}

function HomeScreen(props: { store: Store; onUnpair: () => void; pending?: Pending; history: History }) {
  const c = usePalette();
  const swipe = useBack(props.history);
  useAgentEntry(props.store);
  const open = useSelector((s) => s.page?.agent ?? null);
  const conn = useSelector((s) => s.conn);
  const error = useSelector((s) => s.error);
  const unread = useUnread().length;
  const [tab, setTab] = useHistoryValue<Tab>('home.tab', 'agents');
  const [task, setTask] = useHistoryValue<number | null>('home.task', null);
  const [account, setAccount] = useHistoryValue<string | null>('home.account', null);
  const [page, setPage] = useHistoryValue<Page>('home.page', props.pending?.operation ? 'new' : null);
  const [kept, setKept] = useState<string | null>(props.pending?.operation ?? null);
  const [first, setFirst] = useState(props.pending?.first ?? null);
  const pending: Pending = {
    operation: kept,
    first,
    save: (op, t = null) => (setKept(op), setFirst(t), props.pending?.save(op, t)),
  };
  const toAgent = (name: string) => (setPage(null), void props.store.openAgent(name));
  return (
    <View style={{ flex: 1 }} {...swipe}>
      {conn !== 'open' && <Banner text={conn === 'closed' ? 'Disconnected' : conn === 'connecting' ? 'Connecting…' : 'Reconnecting…'} />}
      {error && <Banner text={error} error onPress={() => props.store.dismissError()} />}
      <View style={{ flex: 1 }}>
        {open ? (
          <AgentPage />
        ) : page === 'worktrees' ? (
          <Worktrees onClose={() => setPage(null)} onAgent={toAgent} />
        ) : page === 'new' ? (
          <NewAgent pending={pending} onClose={() => setPage(null)} onAgent={toAgent} />
        ) : tab === 'inbox' ? (
          <Inbox />
        ) : tab === 'tasks' ? (
          <Tasks selected={task} onSelect={setTask} />
        ) : tab === 'accounts' ? (
          <Accounts selected={account} onSelect={setAccount} />
        ) : tab === 'stats' ? (
          <Stats />
        ) : (
          <AgentList onUnpair={props.onUnpair} onPage={setPage} />
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
              <Text style={{ color: tab === t ? c.ink : c.muted, fontWeight: tab === t ? '700' : '400' }}>
                {t === 'agents'
                  ? 'Agents'
                  : t === 'inbox'
                    ? `Inbox${unread ? ` (${unread})` : ''}`
                    : t === 'tasks'
                      ? 'Tasks'
                      : t === 'accounts'
                        ? 'Accounts'
                        : 'Stats'}
              </Text>
            </Pressable>
          ))}
        </View>
      )}
    </View>
  );
}
