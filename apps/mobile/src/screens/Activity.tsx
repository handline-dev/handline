// Activity tab of the agent page, read-only: the messages between the agents, the task board's notices, and the task, session and tool lines.
// The Chat holds only the person's words and the agent's.
import { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { activity, clockTime, shortName, type TimelineEntry } from '@handline/app-core';
import { useChat } from '@handline/app-data';
import type { TranscriptItem } from '@handline/client';
import { radius, space } from '@handline/tokens';
import { Skeleton } from '../skeleton.tsx';
import { usePalette } from '../theme.ts';
import { ChatEmpty } from './Chat.tsx';
import { SessionMark } from './ChatLines.tsx';
import { Icon } from './Icon.tsx';
import { Button, Row } from './parts.tsx';

const FILTERS = ['All', 'Messages', 'Tasks', 'Sessions', 'Tools'] as const;
type Filter = (typeof FILTERS)[number];

const kindOf = (e: TimelineEntry): Filter =>
  e.kind === 'message' ? 'Messages' : e.kind === 'note' ? 'Tasks' : e.item.kind === 'tool' ? 'Tools' : e.item.kind === 'session' ? 'Sessions' : 'Tasks';

const TEXT = { fontSize: 13 } as const; // one size for every row

export function Activity(props: { agent: string }) {
  const c = usePalette();
  const chat = useChat(props.agent, 'all');
  const mine = useChat(props.agent, 'mine'); // what the Chat draws is not drawn again here
  const team = props.agent.split('.')[0]!;
  const [filter, setFilter] = useState<Filter>('All');
  const [open, setOpen] = useState<string | null>(null);
  const toggle = (k: string) => setOpen(open === k ? null : k);
  if (chat.status === 'off') return <ChatEmpty />;
  const entries = activity(chat.messages, mine.messages, chat.items, props.agent).filter((e) => filter === 'All' || kindOf(e) === filter);
  return (
    <View style={{ flex: 1 }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0 }} contentContainerStyle={{ gap: space.sm, padding: space.md }}>
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
            <Text style={{ ...TEXT, color: filter === f ? c.accent : c.muted, fontWeight: '600' }}>{f}</Text>
          </Pressable>
        ))}
      </ScrollView>
      <ScrollView testID="activity" style={{ flex: 1 }} contentContainerStyle={{ padding: space.md, gap: space.md }}>
        <Skeleton shape="chat" loading={chat.status === 'loading'} label="the activity" />
        {chat.hasOlder && <Button testID="activity-older" label="Older" disabled={chat.loadingOlder} onPress={chat.loadOlder} />}
        {chat.status === 'ready' && !entries.length && (
          <Text testID="activity-empty" style={{ color: c.muted, padding: space.lg }}>
            Nothing here yet
          </Text>
        )}
        {entries.map((e) => {
          if (e.kind === 'item') return e.item.kind === 'session' ? <SessionMark key={`i${e.id}`} i={e.item} /> : e.item.kind === 'tool' ? <ToolRow key={`i${e.id}`} i={e.item} /> : <TaskRow key={`i${e.id}`} i={e.item} />;
          const k = `m${e.id}`;
          return (
            <Pressable key={k} testID={`activity-msg-${e.id}`} accessibilityRole="button" onPress={() => toggle(k)}>
              <Text numberOfLines={open === k ? undefined : 2} style={{ ...TEXT, color: c.ink }}>
                <Text style={{ color: c.muted }}>
                  {clockTime(e.at)} {shortName(e.message.from, team)} → {shortName(e.message.to, team)}{'  '}
                </Text>
                {e.message.body}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

function TaskRow({ i }: { i: TranscriptItem }) {
  const c = usePalette();
  const e = i.task_event;
  return (
    <Row>
      <Icon name="task" color={c.muted} />
      <Text numberOfLines={2} style={{ ...TEXT, color: c.muted, flex: 1 }}>
        T{e?.task} {e?.event ?? 'changed'}
        {e?.evidence ? ` · ${e.evidence}` : ''} · {clockTime(i.at)}
      </Text>
    </Row>
  );
}

/** A tool line: the command, and a tap shows what it printed in place, in full; a second tap folds it. */
function ToolRow({ i }: { i: TranscriptItem }) {
  const c = usePalette();
  const [open, setOpen] = useState(false);
  const cut = i.dropped_lines ?? 0;
  const line = (
    <Row>
      <Icon name="tool" color={c.muted} />
      <Text testID={`tool-${i.id}`} numberOfLines={2} style={{ ...TEXT, color: c.muted, flex: 1 }}>
        {i.tool}
        {i.text ? ` ${i.text}` : ''}
        {!i.output && cut > 0 ? ` · ${cut} more line${cut === 1 ? '' : 's'} let go` : ''}
      </Text>
      {i.output ? <Icon name={open ? 'up' : 'down'} color={c.muted} /> : null}
    </Row>
  );
  if (!i.output) return line;
  return (
    <View style={{ gap: space.xs }}>
      <Pressable testID={`tool-open-${i.id}`} accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => setOpen(!open)}>
        {line}
      </Pressable>
      {open && (
        <>
          <Text testID="tool-output" style={{ ...TEXT, color: c.ink, fontFamily: 'Menlo' }}>
            {i.output}
          </Text>
          {cut > 0 && (
            <Text testID="tool-cut" style={{ ...TEXT, color: c.muted }}>
              {cut} more line{cut === 1 ? '' : 's'} let go
            </Text>
          )}
        </>
      )}
    </View>
  );
}
