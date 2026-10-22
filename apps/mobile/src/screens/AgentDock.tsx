// The agent page's bottom dock: back, menu, then this agent's tabs (Chat or Terminal, and what was opened beside it); hidden while the keyboard is up.
import { useEffect, useRef, useState } from 'react';
import { Keyboard, Platform, Pressable, ScrollView, Text, View } from 'react-native';
import { branchOf } from '@handline/app-core';
import { useWorktrees } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { tabLabel, type AgentTabs } from '#src/screens/AgentTabs.tsx';

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

export function AgentDock(props: { base: string; tabs: AgentTabs; onMenu: () => void; onBack: () => void }) {
  const c = usePalette();
  const { tabs } = props;
  const strip = useRef<ScrollView>(null);
  const at = useRef<Record<string, number>>({});
  const activeKey = tabs.active ?? 'base';
  const reveal = (key: string) => strip.current?.scrollTo({ x: Math.max(0, (at.current[key] ?? 0) - space.lg) });
  useEffect(() => reveal(activeKey), [activeKey]);
  const inset = Platform.OS === 'android' ? 16 : 0; // the gesture bar is drawn over the page
  const btn = { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' } as const;
  const tab = (id: string | null, label: string, closable: boolean) => {
    const on = tabs.active === id;
    return (
      <View key={id ?? 'base'} onLayout={(e) => {
          at.current[id ?? 'base'] = e.nativeEvent.layout.x;
          if (on) reveal(id ?? 'base');
        }} style={{ flexDirection: 'row', alignItems: 'center', borderRadius: radius.pill, backgroundColor: on ? c.panel : 'transparent' }}>
        <Pressable
          testID={`dock-${id ?? 'base'}`}
          accessibilityRole="tab"
          accessibilityState={{ selected: on }}
          onPress={() => tabs.select(id)}
          style={{ height: 32, justifyContent: 'center', paddingLeft: space.md, paddingRight: closable ? space.xs : space.md }}
        >
          <Text numberOfLines={1} style={{ color: on ? c.accent : c.muted, fontWeight: on ? '700' : '500', fontSize: font.size.body }}>
            {label}
          </Text>
        </Pressable>
        {closable ? (
          <Pressable testID={`dock-close-${id}`} accessibilityRole="button" accessibilityLabel={`Close ${label} tab`} hitSlop={8} onPress={() => tabs.close(id!)} style={{ paddingRight: space.sm }}>
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
      <ScrollView ref={strip} horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ alignItems: 'center', gap: space.xs, paddingRight: space.sm }}>
        {tab(null, props.base, false)}
        {tabs.tabs.map((t) => tab(t.id, tabLabel(t), true))}
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
    <Text testID="agent-label" numberOfLines={1} style={{ color: c.muted, fontSize: font.size.label, paddingHorizontal: space.lg, paddingVertical: space.sm }}>
      <Text style={{ color: c.ink, fontWeight: '600' }}>{agent}</Text>
      {branch ? ` · ${branch}` : ''}
    </Text>
  );
}
