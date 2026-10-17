// The row above the messages: Mine or All, and the icon that opens the search box.
import { Pressable, Text, View } from 'react-native';
import { useServes, type ChatScope as Scope } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Row } from './parts.tsx';

export function ScopeRow(props: { scope: Scope; searching: boolean; onScope: (s: Scope) => void; onSearching: (on: boolean) => void }) {
  const c = usePalette();
  const searchServed = useServes('chat.search').served;
  return (
    <Row style={{ paddingHorizontal: space.md, paddingVertical: space.sm }}>
      {(['mine', 'all'] as const).map((s) => (
        <Pressable
          key={s}
          testID={`scope-${s}`}
          accessibilityRole="button"
          accessibilityState={{ selected: props.scope === s }}
          onPress={() => props.onScope(s)}
          style={{
            paddingVertical: space.xs,
            paddingHorizontal: space.md,
            borderRadius: radius.md,
            borderWidth: 1,
            borderColor: props.scope === s ? c.accent : c.line,
          }}
        >
          <Text style={{ color: props.scope === s ? c.accent : c.muted, fontWeight: '600' }}>{s === 'mine' ? 'Mine' : 'All'}</Text>
        </Pressable>
      ))}
      <View style={{ flex: 1 }} />
      {searchServed && (
        <Pressable
          testID="chat-search-open"
          accessibilityRole="button"
          accessibilityLabel="Search messages"
          accessibilityState={{ expanded: props.searching }}
          onPress={() => props.onSearching(!props.searching)}
          hitSlop={8}
        >
          <Text style={{ color: props.searching ? c.accent : c.muted, fontSize: 20 }}>⌕</Text>
        </Pressable>
      )}
    </Row>
  );
}
