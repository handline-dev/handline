// While a session hands off: why and what, live, until the session marker takes its place.
import { Text, View } from 'react-native';
import { ago, handoffLine } from '@handline/app-core';
import type { AgentDetail } from '@handline/client';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';

export function HandoffLine({ handoff }: { handoff: AgentDetail['handoff'] }) {
  const c = usePalette();
  if (!handoff) return null;
  const { why, what } = handoffLine(handoff);
  return (
    <View testID="handoff-line" accessibilityRole="alert" style={{ paddingHorizontal: space.md, paddingVertical: space.xs }}>
      <Text style={{ color: c.muted }}>
        {why} · {what} · {ago(handoff.since)}
      </Text>
    </View>
  );
}
