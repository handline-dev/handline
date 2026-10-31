// "My requests" in the Tasks tab: the founder's own open tasks (not their subtasks), by what is happening to each, every row with one line that
// says how far it is, who works on it now, or what it waits on. A row opens the task as any row of Tasks does. The same list as the PC's.
import { Pressable, ScrollView, Text, View } from 'react-native';
import { taskRef } from '@handline/app-core';
import type { RequestRow } from '@handline/client';
import { useRequests, useTasks } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon, type IconName } from '#src/screens/Icon.tsx';
import { EmptyState, Pill } from '#src/screens/parts.tsx';

const GROUPS: [string, IconName][] = [
  ['Waiting on you', 'needs'],
  ['Blocked', 'paused'],
  ['Being worked', 'working'],
  ['Not taken yet', 'taskPending'],
];

const short = (agent: string) => agent.split('.').at(-1) ?? agent;
const nowOf = (r: RequestRow) => (typeof r.now === 'string' ? r.now : (r.holders[0] ?? null));
const blockedNow = (r: RequestRow) => r.blocked === true;

function groupOf(r: RequestRow): string {
  if (r.status === 'waiting_on_you') return 'Waiting on you';
  if (blockedNow(r)) return 'Blocked';
  return nowOf(r) ? 'Being worked' : 'Not taken yet';
}

/** The founder's open tasks, grouped in the order the list reads. */
export function useMyRequests() {
  const q = useRequests();
  const rows = (q.data?.items ?? []).filter((r) => r.kind === 'task' && r.task != null && r.status !== 'done' && r.status !== 'no_work');
  return {
    rows,
    groups: GROUPS.map(([name, icon]) => ({ name, icon, rows: rows.filter((r) => groupOf(r) === name) })).filter((g) => g.rows.length),
    pending: q.isPending,
  };
}

/** "My requests 6" beside the scope pills; on, it reads "✓ My requests 6 ×" and the × takes it off. */
export function MyRequestsChip(props: { count: number; on: boolean; onToggle: () => void }) {
  const c = usePalette();
  return (
    <Pill
      testID="my-requests-chip"
      on={props.on}
      label={`My requests ${props.count}`}
      onPress={props.onToggle}
      trailing={<Icon name="close" color={c.muted} size={14} decorative />}
    />
  );
}

function Line(props: { r: RequestRow; onTask: (id: number) => void }) {
  const c = usePalette();
  const { r } = props;
  const waits = (useTasks({}).data?.items ?? []).find((t) => t.id === r.task)?.blocked_by?.[0];
  const small = { fontSize: font.size.sm } as const;
  if (blockedNow(r) || waits != null)
    return (
      <View testID="my-request-blocked" style={{ flexDirection: 'row', alignItems: 'center', gap: space.xs }}>
        <Icon name="paused" color={c.wait} size={14} decorative />
        <Text style={{ ...small, color: c.wait }}>{waits != null ? 'Blocked on' : 'Blocked'}</Text>
        {waits != null ? (
          <Pressable testID="my-request-blocker" accessibilityRole="link" onPress={() => props.onTask(waits)} hitSlop={space.sm}>
            <Text style={{ ...small, color: c.wait, textDecorationLine: 'underline' }}>{taskRef(waits)}</Text>
          </Pressable>
        ) : null}
      </View>
    );
  const total = r.open + r.done;
  const line = [
    total ? `${r.done} of ${total} done` : '',
    r.status === 'waiting_on_you' ? 'waiting on you' : nowOf(r) ? `now ${short(nowOf(r)!)}` : 'not taken yet',
    r.status === 'review' ? 'in review' : '',
  ]
    .filter(Boolean)
    .join(' · ');
  return <Text style={{ ...small, color: c.muted }}>{line}</Text>;
}

/** The list the chip turns on, in place of the task list. */
export function MyRequestsList(props: {
  groups: ReturnType<typeof useMyRequests>['groups'];
  pending: boolean;
  onTask: (id: number) => void;
}) {
  const c = usePalette();
  if (props.pending) return <EmptyState text="Loading…" />;
  if (!props.groups.length)
    return <EmptyState text="No open request of yours. What you ask an agent for shows here, with how far it is." />;
  return (
    <ScrollView testID="my-requests">
      {props.groups.map((g) => (
        <View key={g.name} accessibilityLabel={g.name}>
          <View
            accessibilityRole="header"
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: space.sm,
              paddingHorizontal: space.lg,
              paddingTop: space.lg,
              paddingBottom: space.xs,
            }}
          >
            <Icon name={g.icon} color={g.name === 'Blocked' ? c.wait : c.muted} size={16} decorative />
            <Text
              style={{ color: g.name === 'Blocked' ? c.wait : c.muted, fontSize: font.size.sm, fontWeight: '700' }}
            >{`${g.name} ${g.rows.length}`}</Text>
          </View>
          {g.rows.map((r) => (
            <Pressable
              key={r.task}
              testID={`my-request-${r.task}`}
              accessibilityRole="button"
              onPress={() => props.onTask(r.task!)}
              style={{ minHeight: 48, paddingHorizontal: space.lg, paddingVertical: space.sm, gap: 2 }}
            >
              <View style={{ flexDirection: 'row', gap: space.md }}>
                <Text style={{ width: 56, color: c.muted, fontFamily: 'Menlo', fontSize: font.size.sm }}>{taskRef(r.task!)}</Text>
                <Text numberOfLines={1} style={{ flex: 1, color: c.ink }}>
                  {r.title}
                </Text>
              </View>
              <View style={{ paddingLeft: 56 + space.md }}>
                <Line r={r} onTask={props.onTask} />
              </View>
            </Pressable>
          ))}
        </View>
      ))}
    </ScrollView>
  );
}
