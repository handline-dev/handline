// While a session hands off: why and what, live, until the session marker takes its place.
import { useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { ago, handoffLine, restartShown } from '@handline/app-core';
import type { AgentDetail, TranscriptItem } from '@handline/client';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';

export function HandoffLine({ handoff, items }: { handoff: AgentDetail['handoff']; items: TranscriptItem[] }) {
  const c = usePalette();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!handoff || !restartShown(handoff, items)) return null;
  const { why, what } = handoffLine(handoff);
  return (
    <View testID="handoff-line" accessibilityRole="alert" style={{ paddingHorizontal: space.md, paddingVertical: space.xs }}>
      <Text style={{ color: c.muted }}>
        {why} · {what} · {ago(handoff.since, now)}
      </Text>
    </View>
  );
}
