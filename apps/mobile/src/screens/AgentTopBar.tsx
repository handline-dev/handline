// The agent page's top: back, the agent's name with what it is doing in words, the ⋯ menu, and under it one-tap tabs (Chat, Terminal or
// Transcript, Tasks, Files, then the files and tasks opened beside them).
import { useEffect, useRef } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { agentLine } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { StateMark } from '#src/screens/StateMark.tsx';
import { tabLabel, type AgentTab, type AgentTabs } from '#src/screens/AgentTabs.tsx';

const FILES = 'file:';
const TASKS = 'tasks';

export function AgentTopBar(props: {
  agent: string;
  row: AgentView | undefined;
  tabs: AgentTabs;
  /** The page's own view, shown when no opened tab is active. */
  view: 'chat' | 'terminal';
  structured: boolean;
  hasTasks: boolean;
  hasFile: boolean;
  onView: (view: 'chat' | 'terminal') => void;
  onMenu: () => void;
  onBack: () => void;
}) {
  const c = usePalette();
  const { tabs, row } = props;
  const strip = useRef<ScrollView>(null);
  const at = useRef<Record<string, number>>({});
  const activeKey = tabs.active ?? props.view;
  useEffect(() => strip.current?.scrollTo({ x: Math.max(0, (at.current[activeKey] ?? 0) - space.lg) }), [activeKey]);
  const btn = { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' } as const;
  const line = row ? agentLine(row) : '';
  const tab = (key: string, testID: string, label: string, on: boolean, onPress: () => void, close?: () => void) => (
    <View
      key={key}
      onLayout={(e) => void (at.current[key] = e.nativeEvent.layout.x)}
      style={{ flexDirection: 'row', alignItems: 'center', borderRadius: radius.pill, backgroundColor: on ? c.panel : 'transparent' }}
    >
      <Pressable
        testID={testID}
        accessibilityRole="tab"
        accessibilityState={{ selected: on }}
        onPress={onPress}
        style={({ pressed }) => ({
          height: 32,
          justifyContent: 'center',
          paddingLeft: space.md,
          paddingRight: close ? space.xs : space.md,
          borderRadius: radius.pill,
          backgroundColor: pressed ? c.press : undefined,
        })}
      >
        <Text numberOfLines={1} style={{ color: on ? c.accent : c.muted, fontWeight: on ? '700' : '500', fontSize: font.size.body }}>
          {label}
        </Text>
      </Pressable>
      {close ? (
        <Pressable
          testID={`dock-close-${key}`}
          accessibilityRole="button"
          accessibilityLabel={`Close ${label} tab`}
          hitSlop={8}
          onPress={close}
          style={{ paddingRight: space.sm }}
        >
          <Icon name="close" decorative size={14} color={c.muted} />
        </Pressable>
      ) : null}
    </View>
  );
  const page = (view: 'chat' | 'terminal', label: string) =>
    tab(view, `tab-${view}`, label, tabs.active === null && props.view === view, () => (tabs.select(null), props.onView(view)));
  const beside = tabs.tabs.filter((t) => t.id !== TASKS && t.id !== FILES);
  const open = (t: AgentTab) =>
    tab(
      t.id,
      `dock-${t.id}`,
      tabLabel(t),
      tabs.active === t.id,
      () => tabs.select(t.id),
      () => tabs.close(t.id),
    );
  return (
    <View testID="agent-top" style={{ borderBottomWidth: 1, borderBottomColor: c.line, backgroundColor: c.bg }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', height: 48 }}>
        <Pressable testID="back" accessibilityRole="button" accessibilityLabel="Agents list" onPress={props.onBack} style={btn}>
          <Icon name="back" decorative size={22} color={c.accent} />
        </Pressable>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text testID="agent-name" numberOfLines={1} style={{ color: c.ink, fontWeight: '600', fontSize: font.size.body }}>
            {props.agent}
          </Text>
          {row && (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.xs }}>
              <StateMark agent={row} />
              <Text testID="agent-state-line" numberOfLines={1} style={{ flex: 1, color: c.muted, fontSize: font.size.label }}>
                {line}
              </Text>
            </View>
          )}
        </View>
        <Pressable testID="verbs-open" accessibilityRole="button" accessibilityLabel="Menu" onPress={props.onMenu} style={btn}>
          <Icon name="menu" decorative size={22} color={c.ink} />
        </Pressable>
      </View>
      <ScrollView
        ref={strip}
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ alignItems: 'center', gap: space.xs, paddingHorizontal: space.sm, paddingBottom: space.xs }}
      >
        {page('chat', 'Chat')}
        {page('terminal', props.structured ? 'Transcript' : 'Terminal')}
        {props.hasTasks && tab(TASKS, 'tab-tasks', 'Tasks', tabs.active === TASKS, () => tabs.open({ id: TASKS, kind: 'tasks' }))}
        {props.hasFile && tab(FILES, 'tab-file', 'Files', tabs.active === FILES, () => tabs.open({ id: FILES, kind: 'file', path: '' }))}
        {beside.map(open)}
      </ScrollView>
    </View>
  );
}
