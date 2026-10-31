// The top of a task page: the line above the title, what the task blocks, and the Later pill.
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { ago, isPerson, laterKeyOf, laterWords, taskRef } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { TaskDetail } from '@handline/client';
import { useLater, useMyTurn, useTasks } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Pill } from '#src/screens/parts.tsx';
import { LaterSheet } from '#src/screens/RequestsLater.tsx';

const PRIORITY = { 1: 'P1', 2: 'P2', 3: 'P3' } as const;

/** The line above the title: the id, the priority, who asked and who has it (a tap on the second name reassigns), and how long ago. */
export function TaskLine({ t, onAssign, canAssign }: { t: TaskDetail; onAssign: () => void; canAssign: boolean }) {
  const c = usePalette();
  const me = useStore().me;
  const to = isPerson(t.to, me) ? 'you' : t.to;
  const from = isPerson(t.from, me) ? 'you' : t.from;
  const head = [taskRef(t.id), t.priority && PRIORITY[t.priority], from].filter(Boolean).join(' · ');
  return (
    <Text style={{ color: c.muted }}>
      {head} →{' '}
      {canAssign ? (
        <Text testID="assignee-menu" accessibilityRole="button" onPress={onAssign} style={{ color: c.accent }}>
          {to}
        </Text>
      ) : (
        to
      )}{' '}
      · {ago(t.created)}
    </Text>
  );
}

/** The Blocks line: the tasks that wait on this one, only when there are some. */
export function BlocksLine({ id, onOpen }: { id: number; onOpen?: (id: number) => void }) {
  const c = usePalette();
  const blocked = (useTasks({}).data?.items ?? []).filter((x) => x.blocked_by?.includes(id));
  if (!blocked.length) return null;
  return (
    <Text testID="task-blocks" style={{ color: c.muted, fontSize: font.size.sm }}>
      Blocks{' '}
      {blocked.map((x, i) => (
        <Text
          key={x.id}
          testID={`task-blocks-${x.id}`}
          accessibilityRole="link"
          onPress={onOpen ? () => onOpen(x.id) : undefined}
          style={{ color: c.accent }}
        >
          {i ? ' ' : ''}
          {taskRef(x.id)}
        </Text>
      ))}
    </Text>
  );
}

/** The Later pill of a task that is an item of Requests: "Later" opens the choices; once put off it reads "Later: Fri 09:00 ×" and the × brings it back. */
export function LaterPill({ id }: { id: number }) {
  const c = usePalette();
  const turn = useMyTurn();
  const put = useLater();
  const [sheet, setSheet] = useState<number | null>(null);
  const key = `task:${id}`;
  const off = turn.later.find((l) => l.card.key === key);
  const card = off?.card ?? turn.cards.find((x) => laterKeyOf(x) === key);
  if (!card) return null;
  return (
    <>
      <Pill
        testID="task-later"
        on={!!off}
        label={off ? `Later: ${laterWords(off.at)}` : 'Later'}
        onPress={() => setSheet(Date.now())}
        trailing={
          off ? (
            <Pressable testID="task-later-clear" accessibilityLabel="Return now" hitSlop={8} onPress={() => put(key, null)}>
              <Text style={{ color: c.ink, paddingLeft: space.sm }}>×</Text>
            </Pressable>
          ) : null
        }
      />
      {sheet ? <LaterSheet card={card} now={sheet} onPut={(at) => put(key, at)} onClose={() => setSheet(null)} /> : null}
    </>
  );
}

export function Section(props: { title: string; children: React.ReactNode }) {
  const c = usePalette();
  return (
    <View style={{ gap: space.xs }}>
      <Text style={{ color: c.muted, fontSize: font.size.sm, fontWeight: '700' }}>{props.title}</Text>
      {props.children}
    </View>
  );
}
