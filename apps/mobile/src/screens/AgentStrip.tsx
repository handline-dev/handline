// The strip under the agent page's header: model, account, how much of the context window is used and how long the prompt cache lasts.
// A number the daemon cannot measure is a dash; the cache time is counted down here and redrawn when it runs out.
import { useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { useAgentStrip, useServes } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';

const DASH = '–';

/** The cache time left, in the unit that changes: minutes while there are some, then seconds. */
export function cacheText(leftS: number): string {
  if (leftS <= 0) return 'lapsed';
  return leftS >= 60 ? `${Math.ceil(leftS / 60)}m left` : `${leftS}s left`;
}

export function AgentStrip({ agent }: { agent: string }) {
  const c = usePalette();
  const { served } = useServes('agent.strip');
  const q = useAgentStrip(agent);
  const strip = q.data;
  // The cache runs out at a moment, not at a number of seconds: keep that moment and redraw on it.
  const lapsesAt = strip?.cache_left_s == null ? null : q.dataUpdatedAt + strip.cache_left_s * 1000;
  const [tick, redraw] = useState(0);
  useEffect(() => {
    if (lapsesAt === null || lapsesAt <= Date.now()) return;
    // Each second under a minute; above it, at the next whole minute left, where the text changes.
    const ms = lapsesAt - Date.now();
    const t = setTimeout(() => redraw(tick + 1), (ms <= 60_000 ? ms % 1000 || 1000 : ms % 60_000 || 60_000) + 50);
    return () => clearTimeout(t);
  }, [lapsesAt, tick]);
  if (!served || !strip) return null;
  const left = lapsesAt === null ? null : Math.round((lapsesAt - Date.now()) / 1000);
  const cell = (label: string, value: string, testID: string, warn = false) => (
    <Text testID={testID} style={{ color: warn ? c.wait : c.muted, fontSize: 12 }}>
      {label} <Text style={{ color: warn ? c.wait : c.ink, fontWeight: '600' }}>{value}</Text>
    </Text>
  );
  return (
    <View
      testID="agent-strip"
      style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.md, paddingHorizontal: space.md, paddingBottom: space.sm }}
    >
      {cell('model', strip.model ?? DASH, 'strip-model')}
      {cell('acct', strip.account ?? DASH, 'strip-account')}
      {cell('context', strip.ctx_pct == null ? DASH : `${Math.round(strip.ctx_pct)}%`, 'strip-context')}
      {cell('cache', left == null ? DASH : cacheText(left), 'strip-cache', left != null && left <= 0)}
    </View>
  );
}
