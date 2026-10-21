// The agent page's bottom dock: the agents list, the menu, then the pinned agents and the agents opened on this device (at most five unpinned).
// It hides while the keyboard is up so the chat gets the height.
import { useEffect, useState } from 'react';
import { Keyboard, Platform, Pressable, ScrollView, Text, View } from 'react-native';
import { branchOf, shortName } from '@handline/app-core';
import { useNavDock, useOpenTabs, useWorktrees } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import type { AgentView } from '@handline/client';
import { radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { StateMark } from '#src/screens/StateMark.tsx';

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

/** Opens the agent as a tab on this device while its page is showing. */
export function useRememberAgent(name: string): void {
  const { open } = useOpenTabs();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => void (name && open(name)), [name]);
}

export function AgentDock(props: { agent: string; onMenu: () => void; onBack: () => void }) {
  const c = usePalette();
  const store = useStore();
  const { pinned, open } = useNavDock();
  const tabs = useOpenTabs();
  const inset = Platform.OS === 'android' ? 16 : 0; // the gesture bar is drawn over the page
  const btn = { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' } as const;
  const tab = (a: AgentView, closable: boolean) => {
    const on = a.name === props.agent;
    return (
      <View key={a.name} style={{ flexDirection: 'row', alignItems: 'center', borderRadius: radius.pill, backgroundColor: on ? c.panel : 'transparent' }}>
        <Pressable
          testID={`dock-${a.name}`}
          accessibilityRole="tab"
          accessibilityState={{ selected: on }}
          onPress={() => (on ? undefined : void store.openAgent(a.name))}
          style={{ flexDirection: 'row', alignItems: 'center', gap: space.xs, height: 32, paddingLeft: space.md, paddingRight: closable && on ? space.xs : space.md }}
        >
          <StateMark agent={a} />
          <Text numberOfLines={1} style={{ color: on ? c.accent : c.muted, fontWeight: on ? '700' : '500', fontSize: 14 }}>
            {shortName(a.name, a.name.split('.')[0]!)}
          </Text>
        </Pressable>
        {closable && on ? (
          <Pressable testID={`dock-close-${a.name}`} accessibilityRole="button" accessibilityLabel={`Close ${a.name} tab`} hitSlop={8} onPress={() => (tabs.close(a.name), props.onBack())} style={{ paddingRight: space.sm }}>
            <Icon name="close" decorative size={14} color={c.muted} />
          </Pressable>
        ) : null}
      </View>
    );
  };
  return (
    <View testID="agent-dock" style={{ flexDirection: 'row', alignItems: 'center', height: 44 + inset, paddingBottom: inset, borderTopWidth: 1, borderTopColor: c.line, backgroundColor: c.bg }}>
      <Pressable testID="back" accessibilityRole="button" accessibilityLabel="Agents list" onPress={props.onBack} style={btn}>
        <Icon name="back" decorative size={22} color={c.accent} />
      </Pressable>
      <Pressable testID="verbs-open" accessibilityRole="button" accessibilityLabel="Menu" onPress={props.onMenu} style={btn}>
        <Icon name="menu" decorative size={22} color={c.ink} />
      </Pressable>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ alignItems: 'center', gap: space.xs, paddingRight: space.sm }}>
        {pinned.map((i) => tab(i.agent, false))}
        {open.map((i) => tab(i.agent, true))}
      </ScrollView>
    </View>
  );
}

/** `agent · branch`, the line over the menu and over the Terminal and File views. */
export function AgentLabel({ agent }: { agent: string }) {
  const c = usePalette();
  const worktrees = useWorktrees().data?.worktrees ?? [];
  const branch = branchOf(worktrees, agent);
  return (
    <Text testID="agent-label" numberOfLines={1} style={{ color: c.muted, fontSize: 12, paddingHorizontal: space.lg, paddingVertical: space.sm }}>
      <Text style={{ color: c.ink, fontWeight: '600' }}>{agent}</Text>
      {branch ? ` · ${branch}` : ''}
    </Text>
  );
}
