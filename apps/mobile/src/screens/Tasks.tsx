// The Tasks tab: the open tasks the daemon lists (done and discarded are out of the way, as on the web), one task's detail, and the verbs
// of TaskActions: new task, done, reopen, queue, draft, discard, edit, memo, withdraw, cancel. The pills Open / Mine / Closed and the state
// chips narrow the list as on the web.
import { useState } from 'react';
import { useHistoryValue } from '../history.tsx';
import { Linking, SectionList, ScrollView, Text, TextInput, View } from 'react-native';
import { activityOf, ago, CLOSED, defaultFilter, foldEvents, groupTasks, HELD, idleText, shown, sortTasks, TASK_STATES, taskMatches, timeoutText, taskRef, watchLine } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { Task, TaskDetail } from '@handline/client';
import { classify, useMemos, useServes, useTask, useTasks } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Icon, type IconName } from './Icon.tsx';
import { Loadable } from './Loadable.tsx';
import { Button, ListRow, MenuItem, MenuSheet, PageHeader, Pill } from './parts.tsx';
import { CommentBox, CommentRow } from './Comments.tsx';
import { NewTask, TaskVerbs, useTaskPending } from './TaskActions.tsx';
import { TaskGraph } from './TaskGraph.tsx';
import { PrBody } from './PrBody.tsx';
import { TaskBatch } from './TaskBatch.tsx';

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
            <Button testID="tasks-select" label={selecting ? 'Done' : 'Select'} onPress={() => (selecting ? stopSelecting() : setSelecting(true))} />
            <Button testID="new-task-open" label="New task" icon="plus" onPress={() => setCreating(true)} />
            <Button testID="tasks-menu" label="" icon="menu" accessibilityLabel="More" onPress={() => setMenu(!menu)} />
          </>
        }
      />
      {menu && (
        <MenuSheet onClose={() => setMenu(false)}>
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
          <TaskSearch value={search} onChange={setSearch} />
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
        </>
      )}
      {selecting && (
        <View testID="tasks-bar" style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, padding: space.md, borderTopWidth: 1 }}>
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

function TaskSearch(props: { value: string; onChange: (v: string) => void }) {
  const c = usePalette();
  return (
    <View style={{ paddingHorizontal: space.lg, paddingBottom: space.sm }}>
      <TextInput
        testID="tasks-search"
        accessibilityLabel="Search tasks"
        placeholder="Search by number, title or agent"
        placeholderTextColor={c.muted}
        value={props.value}
        onChangeText={props.onChange}
        autoCapitalize="none"
        autoCorrect={false}
        clearButtonMode="while-editing"
        style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: 10, paddingHorizontal: space.md, minHeight: 40 }}
      />
    </View>
  );
}

function GroupHead(props: { title: string; count: number }) {
  const c = usePalette();
  return (
    <Text accessibilityRole="header" style={{ color: c.muted, fontSize: 12, fontWeight: '700', paddingHorizontal: space.lg, paddingTop: space.lg, paddingBottom: space.xs, backgroundColor: c.bg }}>
      {`${props.title} ${props.count}`}
    </Text>
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

/** "3h" for a task not running reads as the time it has: "3h left" (as on the PC row). */
const left = (text: string) => (/^[\dhdm]+$/.test(text) ? `${text} left` : text);

function Row(props: { t: Task; selecting: boolean; marked: boolean; onPress: () => void; onLongPress: () => void }) {
  const t = props.t;
  const c = usePalette();
  const now = Date.now();
  const meta = [taskRef(t.id), `→ ${t.to}`, t.state, idleText(t, now) || ago(t.created)];
  const icon = TASK_ICON[t.state] ?? 'taskPending';
  const time = left(timeoutText(t, now));
  return (
    <ListRow
      testID={`task-${t.id}`}
      lead={
        props.selecting ? (
          <Icon name={props.marked ? 'taskDone' : 'taskDraft'} label={props.marked ? 'Selected' : 'Not selected'} size={18} color={props.marked ? c.ink : c.muted} />
        ) : (
          <Icon name={icon} label={t.state} size={18} color={t.state === 'blocked' ? c.block : c.muted} />
        )
      }
      title={t.title}
      meta={meta.filter(Boolean).join(' · ')}
      selected={props.selecting ? props.marked : undefined}
      trail={
        <View style={{ alignItems: 'flex-end' }}>
          {t.priority ? <Text style={{ color: t.priority === 1 ? c.block : c.muted, fontWeight: '700', fontSize: 12 }}>{PRIORITY[t.priority]}</Text> : null}
          {time ? <Text style={{ color: time.startsWith('over') ? c.block : c.muted, fontSize: 12 }}>{time}</Text> : null}
        </View>
      }
      onPress={props.onPress}
      onLongPress={props.onLongPress}
    />
  );
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
  const sending = useTaskPending(t.id);
  const watch = watchLine(t);
  const line = (s: string, key: string | number = s) => (
    <Text key={key} style={{ color: c.ink }}>
      {s}
    </Text>
  );
  return (
    <ScrollView testID="task-detail" keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: space.lg, gap: space.lg }}>
      <Text style={{ color: c.ink, fontSize: 18, fontWeight: '700' }}>{t.title}</Text>
      {t.body ? <PrBody id={`task-${t.id}`} agent={t.to} body={t.body} /> : null}
      <Text style={{ color: c.muted }}>
        {[`${taskRef(t.id)}`, t.priority && PRIORITY[t.priority], `${t.from} → ${t.to}`, timeoutText(t, now)].filter(Boolean).join(' · ')}
      </Text>
      {watch && (
        <Text testID="task-watch" style={{ color: c.muted }}>
          Watches{' '}
          {watch.url ? (
            <Text style={{ color: c.accent, textDecorationLine: 'underline' }} accessibilityRole="link" onPress={() => void Linking.openURL(watch.url!)}>
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
      <TaskVerbs t={t} />
      {/* The Waits on section already says what holds it; the reason lines are for a task with no listed blocker. */}
      {!t.blockers.length && t.status && HELD.includes(t.status.state) ? <Text style={{ color: c.wait }}>{t.status.text}</Text> : null}
      {!t.status && !t.blockers.length && t.block_reason ? <Text style={{ color: c.block }}>{t.block_reason}</Text> : null}
      {!t.status && !t.blockers.length && t.hold_reason ? <Text style={{ color: c.wait }}>{t.hold_reason}</Text> : null}
      {t.blockers.length ? (
        <Section title="Waits on">{t.blockers.map((b, i) => line(`${taskRef(b.task)} ${b.title} (${b.owner}): ${b.reason}`, i))}</Section>
      ) : null}
      {t.children.length ? <Section title="Subtasks">{t.children.map((k, i) => line(`${taskRef(k)}`, i))}</Section> : null}
      {t.discarded ? (
        <Section title="Closed">
          {line(`${t.discarded.by} discarded it${t.discarded.batch ? ' in a batch' : ''}${t.discarded.withdrew?.length ? `, withdrawing ${t.discarded.withdrew.length} ${t.discarded.withdrew.length === 1 ? 'request' : 'requests'}` : ''}`)}
          {t.discarded.reason ? line(t.discarded.reason, 'why') : null}
        </Section>
      ) : null}
      {t.evidence ? <Section title="Evidence"><PrBody id={`evidence-${t.id}`} agent={t.to} body={t.evidence} /></Section> : null}
      {t.deliveries.length ? (
        <Section title="Delivery">{line(deliverySummary(t.deliveries))}</Section>
      ) : null}
      <Activity t={t} />
    </ScrollView>
  );
}

/** One list of the comments and the events in words (older events fold), and the one comment box under it. */
function Activity({ t }: { t: TaskDetail }) {
  const c = usePalette();
  const memos = useMemos(t.id).data?.memos ?? t.memos; // the memo list is the one a memo write edits at once
  const [all, setAll] = useState(false);
  const { items, hidden } = foldEvents(activityOf(memos, t.timeline), all);
  return (
    <Section title="Activity">
      {hidden ? <Button testID="activity-more" label={`Show ${hidden} earlier ${hidden === 1 ? 'event' : 'events'}`} onPress={() => setAll(true)} /> : null}
      {items.map((i, n) => {
        const m = i.kind === 'comment' ? memos.find((x) => x.id === i.memo) : undefined;
        return m ? (
          <CommentRow key={`${n}-${m.id}`} task={t.id} agent={t.to} memo={m} when={ago(m.at)} />
        ) : (
          <Text key={`${n}-${i.line}`} style={{ color: c.muted }}>{`${i.line} · ${ago(i.at)}`}</Text>
        );
      })}
      <CommentBox id={t.id} />
    </Section>
  );
}
