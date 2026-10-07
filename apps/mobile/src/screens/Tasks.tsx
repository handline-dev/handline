// The Tasks tab: the open tasks the daemon lists (done and discarded are out of the way, as on the web), one task's detail, and the verbs
// of TaskActions: new task, done, reopen, memo, withdraw, cancel.
import { useState } from 'react';
import { FlatList, Pressable, ScrollView, Text, View } from 'react-native';
import { ago, defaultFilter, idleText, shown, sortTasks, timeoutText } from '@handline/app-core';
import type { Task, TaskDetail } from '@handline/client';
import { classify, useMemos, useServes, useTask, useTasks } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';
import { Button } from './parts.tsx';
import { MemoBox, NewTask, TaskVerbs, useTaskPending } from './TaskActions.tsx';

const PRIORITY = { 1: 'P1', 2: 'P2', 3: 'P3' } as const;

export function Tasks(props: { selected: number | null; onSelect: (id: number | null) => void }) {
  return props.selected != null ? <Detail id={props.selected} onBack={() => props.onSelect(null)} /> : <List onSelect={props.onSelect} />;
}

function List(props: { onSelect: (id: number) => void }) {
  const c = usePalette();
  const query = useTasks();
  const { served } = useServes('tasks.list');
  const rows = sortTasks(shown(query.data?.items ?? [], defaultFilter()), 'priority', false, Date.now());
  const [creating, setCreating] = useState(false);
  if (creating)
    return (
      <ScrollView keyboardShouldPersistTaps="handled">
        <NewTask onClose={() => setCreating(false)} />
      </ScrollView>
    );
  return (
    <View style={{ flex: 1 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', padding: space.lg }}>
        <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700', flex: 1 }}>Tasks</Text>
        <Button testID="new-task-open" label="New task" onPress={() => setCreating(true)} style={{ marginRight: space.xl }} />
      </View>
      <Loadable query={query} served={served} isEmpty={rows.length === 0} skeleton="tasks" label="tasks" empty="No open tasks.">
        <FlatList
          data={rows}
          keyExtractor={(t) => String(t.id)}
          renderItem={({ item: t }) => <Row t={t} onPress={() => props.onSelect(t.id)} />}
        />
      </Loadable>
    </View>
  );
}

function Row(props: { t: Task; onPress: () => void }) {
  const c = usePalette();
  const t = props.t;
  const now = Date.now();
  return (
    <Pressable
      testID={`task-${t.id}`}
      onPress={props.onPress}
      style={{
        minHeight: 48,
        justifyContent: 'center',
        paddingHorizontal: space.lg,
        paddingVertical: space.sm,
        borderTopWidth: 1,
        borderTopColor: c.line,
      }}
    >
      <Text numberOfLines={1} style={{ color: c.ink, fontWeight: '600' }}>
        {t.title}
      </Text>
      <Text style={{ color: c.muted, fontSize: 12 }}>
        {[`#${t.id}`, t.state, t.priority && PRIORITY[t.priority], `${t.from} → ${t.to}`, idleText(t, now) || ago(t.created)]
          .filter(Boolean)
          .join(' · ')}
      </Text>
    </Pressable>
  );
}

function Detail(props: { id: number; onBack: () => void }) {
  const c = usePalette();
  const query = useTask(props.id);
  const { served } = useServes('task.get');
  const gone = query.isError && classify(query.error).kind === 'not_found';
  return (
    <View style={{ flex: 1 }}>
      <Button testID="task-back" label="‹ Tasks" onPress={props.onBack} style={{ margin: space.lg, alignSelf: 'flex-start' }} />
      {gone ? (
        <Text testID="empty" style={{ color: c.muted, padding: space.lg }}>
          This task no longer exists.
        </Text>
      ) : (
        <Loadable query={query} served={served} skeleton="taskDetail" label={`task #${props.id}`} empty="This task no longer exists.">
          {query.data ? <Body t={query.data} /> : null}
        </Loadable>
      )}
    </View>
  );
}

function Section(props: { title: string; children: React.ReactNode }) {
  const c = usePalette();
  return (
    <View style={{ gap: space.xs }}>
      <Text style={{ color: c.muted, fontSize: 12, fontWeight: '700' }}>{props.title}</Text>
      {props.children}
    </View>
  );
}

function Body({ t }: { t: TaskDetail }) {
  const c = usePalette();
  const now = Date.now();
  const memos = useMemos(t.id).data?.memos ?? t.memos; // the memo list is the one a memo write edits at once
  const sending = useTaskPending(t.id);
  const line = (s: string) => (
    <Text key={s} style={{ color: c.ink }}>
      {s}
    </Text>
  );
  return (
    <ScrollView testID="task-detail" keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: space.lg, gap: space.lg }}>
      <Text style={{ color: c.ink, fontSize: 18, fontWeight: '700' }}>{t.title}</Text>
      <Text style={{ color: c.muted }}>
        {[`#${t.id}`, t.state, t.priority && PRIORITY[t.priority], `${t.from} → ${t.to}`, timeoutText(t, now)].filter(Boolean).join(' · ')}
      </Text>
      {sending && (
        <Text testID="task-sending" style={{ color: c.muted }}>
          Sending…
        </Text>
      )}
      <TaskVerbs t={t} />
      {t.block_reason ? <Text style={{ color: c.block }}>{t.block_reason}</Text> : null}
      {t.hold_reason ? <Text style={{ color: c.wait }}>{t.hold_reason}</Text> : null}
      {t.body ? <Section title="Brief">{line(t.body)}</Section> : null}
      {t.blockers.length ? (
        <Section title="Waits on">{t.blockers.map((b) => line(`T${b.task} ${b.title} (${b.owner}): ${b.reason}`))}</Section>
      ) : null}
      {t.children.length ? <Section title="Subtasks">{t.children.map((k) => line(`T${k}`))}</Section> : null}
      {t.evidence ? <Section title="Evidence">{line(t.evidence)}</Section> : null}
      {t.deliveries.length ? (
        <Section title="Delivery">{t.deliveries.map((d) => line(`${ago(d.at)} · ${d.how} · ${d.outcome}`))}</Section>
      ) : null}
      {memos.length ? <Section title="Memos">{memos.map((m) => line(`${m.author} ${ago(m.at)}: ${m.text}`))}</Section> : null}
      <MemoBox id={t.id} />
      {t.handoffs.length ? <Section title="Handoffs">{t.handoffs.map((h) => line(`${h.from} → ${h.to}: ${h.note}`))}</Section> : null}
    </ScrollView>
  );
}
