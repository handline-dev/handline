// The Timeline tab of an agent's page: what the daemon recorded about the agent, newest first, with Older fetching the page before the oldest.
import { ScrollView, Text, View } from 'react-native';
import { ago } from '@handline/app-core';
import { classify, sentence, useServes, useTimeline } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';
import { Button } from './parts.tsx';

export function AgentTimeline({ agent }: { agent: string }) {
  const c = usePalette();
  const { query, items, more, loadOlder, loadingOlder, olderError } = useTimeline(agent);
  const { served } = useServes('agent.timeline');
  const now = Date.now();
  return (
    <Loadable
      query={query}
      served={served}
      isEmpty={items.length === 0}
      skeleton="tasks"
      label="the timeline"
      empty={`Nothing is recorded for ${agent} yet.`}
    >
      <ScrollView testID="timeline" contentContainerStyle={{ padding: space.md, gap: space.xs }}>
        {items.map((e) => (
          <View
            key={e.id}
            testID={`timeline-${e.id}`}
            style={{ gap: 2, padding: space.sm, borderWidth: 1, borderColor: c.line, borderRadius: radius.md }}
          >
            <Text style={{ color: c.ink }}>{e.text}</Text>
            <Text style={{ color: c.muted, fontSize: 12 }}>{[e.kind, e.actor?.name, ago(e.at, now)].filter(Boolean).join(' · ')}</Text>
          </View>
        ))}
        {olderError ? (
          <Text testID="timeline-error" accessibilityRole="alert" style={{ color: c.block }}>
            {sentence(classify(olderError))}
          </Text>
        ) : null}
        {more ? (
          <Button
            testID="timeline-older"
            label={loadingOlder ? 'Loading…' : 'Older'}
            disabled={loadingOlder}
            onPress={() => void loadOlder()}
          />
        ) : null}
      </ScrollView>
    </Loadable>
  );
}
