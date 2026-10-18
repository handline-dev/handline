// The Tasks tab: the open tasks the daemon lists (done and discarded are out of the way, as on the web), one task's detail, and the verbs
// of TaskActions: new task, done, reopen, queue, draft, discard, edit, memo, withdraw, cancel. The pills Open / Mine / Closed and the state
// chips narrow the list as on the web.
import { useState } from 'react';
import { useHistoryValue } from '../history.tsx';
import { FlatList, ScrollView, Text, View } from 'react-native';
import { ago, CLOSED, defaultFilter, idleText, shown, sortTasks, TASK_STATES, timeoutText, taskRef } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { Task, TaskDetail } from '@handline/client';
import { classify, useMemos, useServes, useTask, useTasks } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';
import { Button, ListRow, PageHeader, Pill } from './parts.tsx';
import { MemoBox, MemoRow } from './Memos.tsx';
import { NewTask, TaskVerbs, useTaskPending } from './TaskActions.tsx';
import { TaskGraph } from './TaskGraph.tsx';
import { Markdown } from './Markdown.tsx';

type Scope = 'open' | 'mine' | 'closed';
const SCOPES: [Scope, string][] = [
  ['open', 'Open'],
  ['mine', 'Mine'],
  ['closed', 'Closed'],
];

const PRIORITY = { 1: 'P1', 2: 'P2', 3: 'P3' } as const;

export function Tasks(props: { selected: number | null; onSelect: (id: number | null) => void }) {
  return props.selected != null ? <Detail id={props.selected} onBack={() => props.onSelect(null)} /> : <List onSelect={props.onSelect} />;
}

function List(props: { onSelect: (id: number) => void }) {
  const { served } = useServes('tasks.list');
  const me = useStore().me;
  const [scope, setScope] = useHistoryValue<Scope>('tasks.scope', 'open');
  const [states, setStates] = useState<ReadonlySet<string>>(() => defaultFilter().states);
  const query = useTasks(scope === 'closed' ? { all: true } : {});
  const rows = sortTasks(
    shown(query.data?.items ?? [], { ...defaultFilter(), states, closed: scope === 'closed' }).filter((t) =>
      scope === 'mine' ? t.to === me : scope === 'closed' ? CLOSED.includes(t.state) : true,
    ),
    'priority',
    false,
    Date.now(),
  );
  const [creating, setCreating] = useState(false);
  const [graph, setGraph] = useHistoryValue('tasks.graph', false);
  const [menu, setMenu] = useState(false);
  const [filtering, setFiltering] = useState(false);
  const narrowed = states.size !== defaultFilter().states.size;
  if (creating)
    return (
      <ScrollView keyboardShouldPersistTaps="handled">
        <NewTask onClose={() => setCreating(false)} />
      </ScrollView>
    );
  return (
    <View style={{ flex: 1 }}>
      <PageHeader
        title="Tasks"
        actions={
          <>
            <Button testID="new-task-open" label="New task" onPress={() => setCreating(true)} />
            <Button testID="tasks-menu" label="⋯" accessibilityLabel="More" onPress={() => setMenu(!menu)} />
          </>
        }
      />
      {menu && (
        <View style={{ paddingHorizontal: space.lg, paddingBottom: space.sm }}>
          <Button
            testID="graph-toggle"
            label={graph ? 'List' : 'Graph'}
            onPress={() => {
              setGraph(!graph);
              setMenu(false);
            }}
          />
        </View>
      )}
      {graph ? (
        <Loadable query={query} served={served} isEmpty={false} skeleton="tasks" label="tasks" empty="No open tasks.">
          <TaskGraph tasks={query.data?.items ?? []} onSelect={props.onSelect} />
        </Loadable>
      ) : (
        <>
          <View accessibilityRole="toolbar" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, paddingHorizontal: space.lg }}>
            {SCOPES.map(([id, label]) => (
              <Pill key={id} testID={`scope-${id}`} on={scope === id} label={label} onPress={() => setScope(id)} />
            ))}
            {scope !== 'closed' && (
              <Pill
                testID="filter-toggle"
                on={filtering || narrowed}
                label="Filter"
                onPress={() => setFiltering(!filtering)}
              />
            )}
          </View>
          {scope !== 'closed' && filtering && (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, padding: space.lg }}>
              {TASK_STATES.filter((s) => !CLOSED.includes(s)).map((s) => (
                <Pill
                  key={s}
                  testID={`state-${s}`}
                  on={states.has(s)}
                  label={s}
                  onPress={() => {
                    const next = new Set(states);
                    if (!next.delete(s)) next.add(s);
                    setStates(next);
                  }}
                />
              ))}
            </View>
          )}
          <Loadable
            query={query}
            served={served}
            isEmpty={rows.length === 0}
            skeleton="tasks"
            label="tasks"
            empty={scope === 'closed' ? 'No closed tasks.' : 'No open tasks.'}
          >
            <FlatList
              data={rows}
              keyExtractor={(t) => String(t.id)}
              renderItem={({ item: t }) => <Row t={t} onPress={() => props.onSelect(t.id)} />}
            />
          </Loadable>
        </>
      )}
    </View>
  );
}

function Row(props: { t: Task; onPress: () => void }) {
  const t = props.t;
  const now = Date.now();
  const meta = [`→ ${t.to}`, t.state, idleText(t, now) || ago(t.created)];
  return <ListRow testID={`task-${t.id}`} title={t.title} meta={meta.filter(Boolean).join(' · ')} onPress={props.onPress} />;
}

export function Detail(props: { id: number; onBack: () => void }) {
  const c = usePalette();
  const query = useTask(props.id);
  const { served } = useServes('task.get');
  const gone = query.isError && classify(query.error).kind === 'not_found';
  return (
    <View style={{ flex: 1 }}>
      <PageHeader back={{ label: 'Tasks', onPress: props.onBack, testID: 'task-back' }} />
      {gone ? (
        <Text testID="empty" style={{ color: c.muted, padding: space.lg }}>
          This task no longer exists.
        </Text>
      ) : (
        <Loadable query={query} served={served} skeleton="taskDetail" label={`task ${taskRef(props.id)}`} empty="This task no longer exists.">
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

/** The last delivery and how many there were, not one line per attempt. */
function deliverySummary(ds: TaskDetail['deliveries']): string {
  const d = [...ds].sort((a, b) => Date.parse(b.at) - Date.parse(a.at))[0]!;
  return `${ds.length > 1 ? `${ds.length} deliveries, last: ` : ''}${ago(d.at)} · ${d.how} · ${d.outcome}`;
}

function Body({ t }: { t: TaskDetail }) {
  const c = usePalette();
  const now = Date.now();
  const memos = useMemos(t.id).data?.memos ?? t.memos; // the memo list is the one a memo write edits at once
  const sending = useTaskPending(t.id);
  const line = (s: string, key: string | number = s) => (
    <Text key={key} style={{ color: c.ink }}>
      {s}
    </Text>
  );
  return (
    <ScrollView testID="task-detail" keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: space.lg, gap: space.lg }}>
      <Text style={{ color: c.ink, fontSize: 18, fontWeight: '700' }}>{t.title}</Text>
      <Text style={{ color: c.muted }}>
        {[`${taskRef(t.id)}`, t.state, t.priority && PRIORITY[t.priority], `${t.from} → ${t.to}`, timeoutText(t, now)].filter(Boolean).join(' · ')}
      </Text>
      {sending && (
        <Text testID="task-sending" style={{ color: c.muted }}>
          Sending…
        </Text>
      )}
      <TaskVerbs t={t} />
      {/* The Waits on section already says what holds it; the reason lines are for a task with no listed blocker. */}
      {!t.blockers.length && t.block_reason ? <Text style={{ color: c.block }}>{t.block_reason}</Text> : null}
      {!t.blockers.length && t.hold_reason ? <Text style={{ color: c.wait }}>{t.hold_reason}</Text> : null}
      {t.body ? <Section title="Brief"><Markdown text={t.body} breaks /></Section> : null}
      {t.blockers.length ? (
        <Section title="Waits on">{t.blockers.map((b, i) => line(`${taskRef(b.task)} ${b.title} (${b.owner}): ${b.reason}`, i))}</Section>
      ) : null}
      {t.children.length ? <Section title="Subtasks">{t.children.map((k, i) => line(`${taskRef(k)}`, i))}</Section> : null}
      {t.evidence ? <Section title="Evidence"><Markdown text={t.evidence} breaks /></Section> : null}
      {t.deliveries.length ? (
        <Section title="Delivery">{line(deliverySummary(t.deliveries))}</Section>
      ) : null}
      {memos.length ? (
        <Section title="Memos">
          {memos.map((m) => (
            <MemoRow key={m.id} task={t.id} memo={m} when={ago(m.at)} />
          ))}
        </Section>
      ) : null}
      <MemoBox id={t.id} />
      {t.handoffs.length ? <Section title="Handoffs">{t.handoffs.map((h, i) => line(`${h.from} → ${h.to}: ${h.note}`, i))}</Section> : null}
    </ScrollView>
  );
}
