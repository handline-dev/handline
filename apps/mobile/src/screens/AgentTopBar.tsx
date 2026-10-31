// The agent page's top: back, the agent's name with what it is doing in words, and the ⋯ menu. Terminal, Files and Tasks open full screen from
// the menu; on one of them the name is that view's and back returns to the chat.
import { Pressable, Text, View } from 'react-native';
import { agentLine } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { font } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { StateMark } from '#src/screens/StateMark.tsx';

export function AgentTopBar(props: {
  agent: string;
  row: AgentView | undefined;
  /** The full-screen view open over the chat (Terminal, Files, Tasks); its name replaces the agent's state line. */
  view: string | null;
  onMenu: () => void;
  onBack: () => void;
}) {
  const c = usePalette();
  const { row } = props;
  const btn = { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' } as const;
  const line = row ? agentLine(row) : '';
  return (
    <View testID="agent-top" style={{ borderBottomWidth: 1, borderBottomColor: c.line, backgroundColor: c.bg }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', height: 48 }}>
        <Pressable
          testID="back"
          accessibilityRole="button"
          accessibilityLabel={props.view ? 'Back to the chat' : 'Agents list'}
          onPress={props.onBack}
          style={btn}
        >
          <Icon name="back" decorative size={22} color={c.accent} />
        </Pressable>
        <View style={{ flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text testID="agent-name" numberOfLines={1} style={{ flexShrink: 1, color: c.ink, fontWeight: '600', fontSize: font.size.md }}>
            {props.agent}
          </Text>
          {props.view ? (
            <Text testID="agent-view" numberOfLines={1} style={{ color: c.muted, fontSize: font.size.sm }}>
              {props.view}
            </Text>
          ) : (
            row && (
              <View style={{ flexShrink: 1, flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                <StateMark agent={row} />
                <Text testID="agent-state-line" numberOfLines={1} style={{ flexShrink: 1, color: c.muted, fontSize: font.size.sm }}>
                  {line}
                </Text>
              </View>
            )
          )}
        </View>
        <Pressable testID="verbs-open" accessibilityRole="button" accessibilityLabel="Menu" onPress={props.onMenu} style={btn}>
          <Icon name="menu" decorative size={22} color={c.ink} />
        </Pressable>
      </View>
    </View>
  );
}
