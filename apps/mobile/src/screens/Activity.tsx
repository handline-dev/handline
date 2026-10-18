// Activity tab of the agent page: what the agent does with the team, read-only. The messages between the agents, the task board's notices,
// and the task, session and tool lines; the Chat holds only the person's words and the agent's.
import { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { activity, clockTime, type TimelineEntry } from '@handline/app-core';
import { useChat } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { Skeleton } from '../skeleton.tsx';
import { usePalette } from '../theme.ts';
import { Line } from './ChatLines.tsx';
import { ChatEmpty } from './Chat.tsx';
import { Button, Row } from './parts.tsx';

const FILTERS = ['All', 'Messages', 'Tasks', 'Sessions', 'Tools'] as const;
type Filter = (typeof FILTERS)[number];

const kindOf = (e: TimelineEntry): Filter => (e.kind === 'message' ? 'Messages' : e.kind === 'note' ? 'Tasks' : e.item.kind === 'tool' ? 'Tools' : e.item.kind === 'session' ? 'Sessions' : 'Tasks');

export function Activity(props: { agent: string }) {
  const c = usePalette();
  const chat = useChat(props.agent, 'all');
  const mine = useChat(props.agent, 'mine'); // what the Chat draws is not drawn again here
  const [filter, setFilter] = useState<Filter>('All');
  const [open, setOpen] = useState<number | null>(null);
  if (chat.status === 'off') return <ChatEmpty />;
  const entries = activity(chat.messages, mine.messages, chat.items, props.agent).filter((e) => filter === 'All' || kindOf(e) === filter);
  return (
    <View style={{ flex: 1 }}>
      <Row style={{ paddingHorizontal: space.md, paddingVertical: space.sm, flexWrap: 'wrap' }}>
        {FILTERS.map((f) => (
          <Pressable
            key={f}
            testID={`activity-${f.toLowerCase()}`}
            accessibilityRole="button"
            accessibilityState={{ selected: filter === f }}
            onPress={() => setFilter(f)}
            style={{
              paddingVertical: space.xs,
              paddingHorizontal: space.md,
              borderRadius: radius.md,
              borderWidth: 1,
              borderColor: filter === f ? c.accent : c.line,
            }}
          >
            <Text style={{ color: filter === f ? c.accent : c.muted, fontWeight: '600' }}>{f}</Text>
          </Pressable>
        ))}
      </Row>
      <Text style={{ color: c.muted, fontSize: 11, paddingHorizontal: space.md }}>What {props.agent} does with the team. Read-only: to steer the team, write in Chat.</Text>
      <ScrollView testID="activity" style={{ flex: 1 }} contentContainerStyle={{ padding: space.sm, gap: space.sm }}>
        <Skeleton shape="chat" loading={chat.status === 'loading'} label="the activity" />
        {chat.hasOlder && <Button testID="activity-older" label="Older" disabled={chat.loadingOlder} onPress={chat.loadOlder} />}
        {chat.status === 'ready' && !entries.length && (
          <Text testID="activity-empty" style={{ color: c.muted, padding: space.lg }}>
            Nothing here yet
          </Text>
        )}
        {entries.map((e) =>
          e.kind === 'item' ? (
            <Line key={`i${e.id}`} i={e.item} />
          ) : (
            <Pressable key={`m${e.id}`} testID={`activity-msg-${e.id}`} accessibilityRole="button" onPress={() => setOpen(open === e.id ? null : e.id)}>
              <Text style={{ color: c.muted, fontSize: 12 }}>
                {clockTime(e.at)} · {e.message.from} → {e.message.to}
              </Text>
              <Text numberOfLines={open === e.id ? undefined : 2} style={{ color: c.ink }}>
                {e.message.body}
              </Text>
            </Pressable>
          ),
        )}
      </ScrollView>
    </View>
  );
}
