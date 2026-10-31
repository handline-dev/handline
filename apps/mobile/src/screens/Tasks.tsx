// The Tasks tab: the open tasks the daemon lists (done and discarded are out of the way, as on the web), one task's detail, and the verbs
// of TaskActions: new task, done, reopen, queue, draft, discard, edit, memo, withdraw, cancel. The pills Open / Assigned to me / Closed and the state
// chips narrow the list as on the web.
import { useState } from 'react';
import {
  ago,
  CLOSED,
  dueOf,
  defaultFilter,
  groupTasks,
  HELD,
  idleText,
  isGate,
  shown,
  sortTasks,
  TASK_STATES,
  taskMatches,
  taskRef,
  timeLeft,
  timeoutText,
  watchLine,
  watchResult,
} from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { Task, TaskDetail } from '@handline/client';
import { classify, useServes, useTask, useTasks } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { useHistoryValue } from '#src/history.tsx';
import { Linking, SectionList, ScrollView, Text, View } from 'react-native';
import { usePalette } from '#src/theme.ts';
import { Icon, type IconName } from '#src/screens/Icon.tsx';
import { Loadable } from '#src/screens/Loadable.tsx';
import { GroupHead } from '#src/screens/TaskGroupHead.tsx';
import { Button, EmptyState, ListRow, MenuItem, MenuSheet, PageHeader, Pill } from '#src/screens/parts.tsx';
import { Activity } from '#src/screens/TaskActivity.tsx';
import { TaskVerbs, useTaskPending } from '#src/screens/TaskActions.tsx';
import { NewTask } from '#src/screens/TaskForm.tsx';
import { TaskTimeLine } from '#src/screens/TaskTime.tsx';
import { BlocksLine, LaterPill, Section, TaskLine } from '#src/screens/TaskHead.tsx';
import { TaskGraph } from '#src/screens/TaskGraph.tsx';
import { PrBody } from '#src/screens/PrBody.tsx';
import { TaskBatch } from '#src/screens/TaskBatch.tsx';
import { TaskSearch } from '#src/screens/TaskSearch.tsx';
import { MyRequestsChip, MyRequestsList, useMyRequests } from '#src/screens/MyRequests.tsx';

type Scope = 'open' | 'mine' | 'closed';
const SCOPES: [Scope, string][] = [
  ['open', 'Open'],
  ['mine', 'Assigned to me'],
  ['closed', 'Closed'],
];

const PRIORITY = { 1: 'P1', 2: 'P2', 3: 'P3' } as const;

export function Tasks(props: { selected: number | null; onSelect: (id: number | null) => void }) {
  return props.selected != null ? (
    <Detail id={props.selected} onBack={() => props.onSelect(null)} onOpen={props.onSelect} />
  ) : (
    <List onSelect={props.onSelect} />
  );
}

function List(props: { onSelect: (id: number) => void }) {
  const { served } = useServes('tasks.list');
  const me = useStore().me;
  const [scope, setScope] = useHistoryValue<Scope>('tasks.scope', 'open');
  const [states, setStates] = useState<ReadonlySet<string>>(() => defaultFilter().states);
  const [search, setSearch] = useState('');
  const query = useTasks(scope === 'closed' ? { all: true } : {});
  const rows = sortTasks(
    shown(query.data?.items ?? [], { ...defaultFilter(), states, closed: scope === 'closed' })
      .filter((t) => (scope === 'mine' ? t.to === me : scope === 'closed' ? CLOSED.includes(t.state) : true))
      .filter(taskMatches(search)),
    'priority',
    false,
    Date.now(),
  );
  const [creating, setCreating] = useState(false);
  const [marked, setMarked] = useState<ReadonlySet<number>>(new Set());
  const [acting, setActing] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const stopSelecting = () => (setSelecting(false), setMarked(new Set()));
  const toggle = (id: number) => {
    setSelecting(true);
    const next = new Set(marked);
    if (!next.delete(id)) next.add(id);
    setMarked(next);
  };
  const sections = groupTasks(rows, me).map((g) => ({ key: g.key, title: g.label, data: g.rows }));
  const [graph, setGraph] = useHistoryValue('tasks.graph', false);
  const [mineOn, setMineOn] = useHistoryValue('tasks.myRequests', false);
  const my = useMyRequests();
  const myList = mineOn;
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
            {selecting ? <Button testID="tasks-select" label="Done" onPress={stopSelecting} /> : null}
            <Button testID="new-task-open" label="" icon="plus" accessibilityLabel="New task" onPress={() => setCreating(true)} />
            <Button testID="tasks-menu" label="" icon="menu" accessibilityLabel="More" onPress={() => setMenu(!menu)} />
          </>
        }
      />
      {menu && (
        <MenuSheet title="Tasks" onClose={() => setMenu(false)}>
          <MenuItem testID="tasks-select" icon="done" label="Select" onPress={() => (setMenu(false), setSelecting(true))} />
          <MenuItem
            testID="graph-toggle"
            icon={graph ? 'viewList' : 'viewTree'}
            label={graph ? 'List' : 'Graph'}
            onPress={() => {
              setGraph(!graph);
              setMenu(false);
            }}
          />
        </MenuSheet>
      )}
      {graph ? (
        <Loadable query={query} served={served} isEmpty={false} skeleton="tasks" label="tasks" empty="No open tasks.">
          <TaskGraph tasks={query.data?.items ?? []} onSelect={props.onSelect} />
        </Loadable>
      ) : (
        <>
          {!myList && <TaskSearch value={search} onChange={setSearch} />}
          <View accessibilityRole="toolbar" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, paddingHorizontal: space.lg }}>
            {SCOPES.map(([id, label]) => (
              <Pill
                key={id}
                testID={`scope-${id}`}
                on={scope === id}
                label={label}
                onPress={() => (setScope(id), id !== 'open' && setMineOn(false))}
              />
            ))}
            <MyRequestsChip count={my.rows.length} on={mineOn} onToggle={() => (setMineOn(!mineOn), setScope('open'))} />
            {scope !== 'closed' && (
              <Pill testID="filter-toggle" on={filtering || narrowed} label="Filter" onPress={() => setFiltering(!filtering)} />
            )}
          </View>
          {scope !== 'closed' && !myList && filtering && (
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
          {myList ? (
            <MyRequestsList groups={my.groups} pending={my.pending} onTask={props.onSelect} />
          ) : (
            <Loadable
              query={query}
              served={served}
              isEmpty={rows.length === 0}
              skeleton="tasks"
              label="tasks"
              empty={scope === 'closed' ? 'No closed tasks.' : 'No open tasks.'}
            >
              <SectionList
                sections={sections}
                keyExtractor={(t) => String(t.id)}
                renderSectionHeader={({ section }) => <GroupHead title={section.title} count={section.data.length} />}
                renderItem={({ item: t }) => (
                  <Row
                    t={t}
                    selecting={selecting}
                    marked={marked.has(t.id)}
                    onPress={() => (selecting ? toggle(t.id) : props.onSelect(t.id))}
                    onLongPress={() => toggle(t.id)}
                  />
                )}
              />
            </Loadable>
          )}
        </>
      )}
      {selecting && (
        <View
          testID="tasks-bar"
          style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, padding: space.md, borderTopWidth: 1 }}
        >
          <Text style={{ flex: 1, fontWeight: '600' }}>{`${marked.size} selected`}</Text>
          <Button testID="tasks-bar-actions" label="Actions" disabled={!marked.size} onPress={() => setActing(true)} />
        </View>
      )}
      {acting && (
        <TaskBatch
          tasks={rows.filter((t) => marked.has(t.id))}
          onClose={() => setActing(false)}
          onDone={() => (setActing(false), stopSelecting())}
        />
      )}
    </View>
  );
}

const TASK_ICON: Record<string, IconName> = {
  draft: 'taskDraft',
  queued: 'taskQueued',
  doing: 'taskDoing',
  blocked: 'taskBlocked',
  done: 'taskDone',
  discarded: 'taskDiscarded',
  pending: 'taskPending',
  review: 'taskPending',
};

function Row(props: { t: Task; selecting: boolean; marked: boolean; onPress: () => void; onLongPress: () => void }) {
  const t = props.t;
  const c = usePalette();
  const now = Date.now();
  const meta = [taskRef(t.id), `→ ${t.to}`, t.state, idleText(t, now) || ago(t.created)];
  const failed = t.state === 'done' && watchResult(t) === 'failed';
  const timedOut = t.state === 'done' && watchResult(t) === 'timed_out';
  const icon = failed ? 'failed' : timedOut ? 'waiting' : (TASK_ICON[t.state] ?? 'taskPending');
  const time = timeLeft(timeoutText(t, now));
  return (
    <ListRow
      testID={`task-${t.id}`}
      lead={
        props.selecting ? (
          <Icon
            name={props.marked ? 'taskDone' : 'taskDraft'}
            label={props.marked ? 'Selected' : 'Not selected'}
            size={18}
            color={props.marked ? c.ink : c.muted}
          />
        ) : (
          <Icon name={icon} label={t.state} size={18} color={t.state === 'blocked' || failed ? c.block : c.muted} />
        )
      }
      title={t.title}
      meta={meta.filter(Boolean).join(' · ')}
      selected={props.selecting ? props.marked : undefined}
      trail={
        <View style={{ alignItems: 'flex-end' }}>
          {t.priority ? (
            <Text style={{ color: t.priority === 1 ? c.block : c.muted, fontWeight: '700', fontSize: font.size.sm }}>
              {PRIORITY[t.priority]}
            </Text>
          ) : null}
          {time ? <Text style={{ color: time.startsWith('over') ? c.block : c.muted, fontSize: font.size.sm }}>{time}</Text> : null}
        </View>
      }
      onPress={props.onPress}
      onLongPress={props.onLongPress}
    />
  );
}

export function Detail(props: { id: number; onBack: () => void; backLabel?: string; onOpen?: (id: number) => void }) {
  const query = useTask(props.id);
  const { served } = useServes('task.get');
  const gone = query.isError && classify(query.error).kind === 'not_found';
  return (
    <View style={{ flex: 1 }}>
      {query.data ? null : <PageHeader back={{ label: props.backLabel ?? 'Tasks', onPress: props.onBack, testID: 'task-back' }} />}
      {gone ? (
        <EmptyState text="This task no longer exists." />
      ) : (
        <Loadable
          query={query}
          served={served}
          skeleton="taskDetail"
          label={`task ${taskRef(props.id)}`}
          empty="This task no longer exists."
        >
          {query.data ? (
            <Body
              t={query.data}
              onOpen={props.onOpen}
              back={{ label: props.backLabel ?? 'Tasks', onPress: props.onBack, testID: 'task-back' }}
            />
          ) : null}
        </Loadable>
      )}
    </View>
  );
}

/** The last delivery and how many there were, not one line per attempt. */
function deliverySummary(ds: TaskDetail['deliveries']): string {
  const d = [...ds].sort((a, b) => Date.parse(b.at) - Date.parse(a.at))[0]!;
  return `${ds.length > 1 ? `${ds.length} deliveries, last: ` : ''}${ago(d.at)} · ${d.how} · ${d.outcome}`;
}

function Body({
  t,
  back,
  onOpen,
}: {
  t: TaskDetail;
  onOpen?: (id: number) => void;
  back: { label: string; onPress: () => void; testID: string };
}) {
  const c = usePalette();
  const now = Date.now();
  const sending = useTaskPending(t.id);
  const watch = watchLine(t);
  const due = dueOf(t, now);
  const line = (s: string, key: string | number = s) => (
    <Text key={key} style={{ color: c.ink }}>
      {s}
    </Text>
  );
  return (
    <TaskVerbs t={t} later={<LaterPill id={t.id} />}>
      {({ pills, more, bar, assign }) => (
        <>
          <PageHeader back={back} actions={more} />
          <ScrollView
            testID="task-detail"
            automaticallyAdjustKeyboardInsets
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={{ padding: space.lg, paddingTop: 0, gap: space.lg }}
          >
            <TaskLine t={t} canAssign={!CLOSED.includes(t.state) && !isGate(t)} onAssign={assign} />
            <Text style={{ color: c.ink, fontSize: font.size.xl, fontWeight: '700' }}>{t.title}</Text>
            <BlocksLine id={t.id} onOpen={onOpen} />
            {t.body ? <PrBody id={`task-${t.id}`} agent={t.to} body={t.body} /> : null}
            {pills}
            <TaskTimeLine id={t.id} left={due?.text} over={due?.over} running={t.state === 'doing'} />
            {watch && (
              <Text testID="task-watch" style={{ color: c.muted }}>
                Watches{' '}
                {watch.url ? (
                  <Text
                    style={{ color: c.accent, textDecorationLine: 'underline' }}
                    accessibilityRole="link"
                    onPress={() => void Linking.openURL(watch.url!)}
                  >
                    {watch.text}
                  </Text>
                ) : (
                  watch.text
                )}
                {watch.result ? ` · ${watch.result}` : ''}
              </Text>
            )}
            {sending && (
              <Text testID="task-sending" style={{ color: c.muted }}>
                Sending…
              </Text>
            )}
            {/* The Waits on section already says what holds it; the reason lines are for a task with no listed blocker. */}
            {!t.blockers.length && t.status && HELD.includes(t.status.state) ? (
              <Text style={{ color: c.wait }}>{t.status.text}</Text>
            ) : null}
            {!t.status && !t.blockers.length && t.block_reason ? <Text style={{ color: c.block }}>{t.block_reason}</Text> : null}
            {!t.status && !t.blockers.length && t.hold_reason ? <Text style={{ color: c.wait }}>{t.hold_reason}</Text> : null}
            {t.blockers.length ? (
              <Section title="Waits on">
                {t.blockers.map((b, i) => line(`${taskRef(b.task)} ${b.title} (${b.owner}): ${b.reason}`, i))}
              </Section>
            ) : null}
            {t.children.length ? <Section title="Subtasks">{t.children.map((k, i) => line(`${taskRef(k)}`, i))}</Section> : null}
            {t.discarded ? (
              <Section title="Closed">
                {line(
                  `${t.discarded.by} discarded it${t.discarded.batch ? ' in a batch' : ''}${t.discarded.withdrew?.length ? `, withdrawing ${t.discarded.withdrew.length} ${t.discarded.withdrew.length === 1 ? 'request' : 'requests'}` : ''}`,
                )}
                {t.discarded.reason ? line(t.discarded.reason, 'why') : null}
              </Section>
            ) : null}
            {t.evidence ? (
              <Section title="Evidence">
                <PrBody id={`evidence-${t.id}`} agent={t.to} body={t.evidence} />
              </Section>
            ) : null}
            {t.deliveries.length ? <Section title="Delivery">{line(deliverySummary(t.deliveries))}</Section> : null}
            <Activity t={t} />
          </ScrollView>
          {bar}
        </>
      )}
    </TaskVerbs>
  );
}
