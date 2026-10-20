// The agent page's bottom dock: the agents list, the menu, and a tab for each agent opened this session (like the PC's session tabs).
// It hides while the keyboard is up so the chat gets the height.
import { useEffect, useState, useSyncExternalStore } from 'react';
import { Keyboard, Platform, Pressable, ScrollView, Text, View } from 'react-native';
import { shortName } from '@handline/app-core';
import { useAgents } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Icon } from './Icon.tsx';
import { StateMark } from './StateMark.tsx';

let opened: string[] = [];
const listeners = new Set<() => void>();
const subscribe = (l: () => void) => (listeners.add(l), () => void listeners.delete(l));

/** Remember an agent as opened this session; each stays once, in the order first opened. */
export function rememberAgent(name: string): void {
  if (name === '' || opened.includes(name)) return;
  opened = [...opened, name];
  listeners.forEach((l) => l());
}

export function useKeyboardUp(): boolean {
  const [up, setUp] = useState(false);
  useEffect(() => {
    const ios = Platform.OS === 'ios';
    const show = Keyboard.addListener(ios ? 'keyboardWillShow' : 'keyboardDidShow', () => setUp(true));
    const hide = Keyboard.addListener(ios ? 'keyboardWillHide' : 'keyboardDidHide', () => setUp(false));
    return () => (show.remove(), hide.remove());
  }, []);
  return up;
}

export function AgentDock(props: { agent: string; onMenu: () => void; onBack: () => void }) {
  const c = usePalette();
  const store = useStore();
  const names = useSyncExternalStore(subscribe, () => opened);
  const rows = useAgents().data?.agents ?? [];
  const inset = Platform.OS === 'android' ? 16 : 0; // the gesture bar is drawn over the page
  const btn = { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' } as const;
  return (
    <View testID="agent-dock" style={{ flexDirection: 'row', alignItems: 'center', height: 44 + inset, paddingBottom: inset, borderTopWidth: 1, borderTopColor: c.line, backgroundColor: c.bg }}>
      <Pressable testID="back" accessibilityRole="button" accessibilityLabel="Agents list" onPress={props.onBack} style={btn}>
        <Icon name="back" decorative size={22} color={c.accent} />
      </Pressable>
      <Pressable testID="verbs-open" accessibilityRole="button" accessibilityLabel="Menu" onPress={props.onMenu} style={btn}>
        <Icon name="menu" decorative size={22} color={c.ink} />
      </Pressable>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ alignItems: 'center', gap: space.xs, paddingRight: space.sm }}>
        {names.map((n) => {
          const row = rows.find((a) => a.name === n);
          const on = n === props.agent;
          return (
            <Pressable
              key={n}
              testID={`dock-${n}`}
              accessibilityRole="tab"
              accessibilityState={{ selected: on }}
              onPress={() => (on ? undefined : void store.openAgent(n))}
              style={{ flexDirection: 'row', alignItems: 'center', gap: space.xs, height: 32, paddingHorizontal: space.md, borderRadius: radius.pill, backgroundColor: on ? c.panel : 'transparent' }}
            >
              <StateMark agent={{ state: row?.state ?? 'unknown', has_dialog: row?.has_dialog ?? false, block: row?.block }} />
              <Text numberOfLines={1} style={{ color: on ? c.accent : c.muted, fontWeight: on ? '700' : '500', fontSize: 14 }}>
                {shortName(n, n.split('.')[0]!)}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}
