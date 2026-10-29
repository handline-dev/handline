// A task's change history on the phone (docs/pc-app-gaps.md U3), read from task.history through the app-core view the web draws.
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import {
  clockTime,
  dayLabel,
  firstLine,
  historyCounts,
  historyView,
  memoAction,
  type HistoryFilter,
  type HistoryFold,
} from '@handline/app-core';
import type { HistoryRow } from '@handline/client';
import { useServes, useTaskHistory } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';

const FILTERS: [HistoryFilter, string][] = [
  ['all', 'All'],
  ['fields', 'Fields'],
  ['state', 'State'],
  ['memo', 'Comment'],
];
const FIELD_WORD: Record<string, string> = { timeout: 'time box', blocked_by: 'waits on', merged_into: 'merged into' };

function Change({ r, shown }: { r: HistoryRow; shown: boolean }) {
  const c = usePalette();
  const [open, setOpen] = useState(false);
  const memo = r.field === 'memo';
  const action = memo ? memoAction(r) : null;
  const was = firstLine(r.was);
  const now = firstLine(r.now);
  const long = was.more || now.more;
  const label = memo ? `comment #${r.memo}` : (FIELD_WORD[r.field] ?? r.field);
  const struck = { color: c.muted, textDecorationLine: 'line-through' as const };
  return (
    <Pressable
      testID={`history-${r.field}`}
      disabled={!long}
      onPress={() => setOpen(!open)}
      accessibilityRole={long ? 'button' : undefined}
      accessibilityState={long ? { expanded: open } : undefined}
    >
      <Text style={{ color: c.ink }}>
        <Text style={{ color: c.muted }}>{shown ? `${clockTime(r.at)}  ` : ''}</Text>
        {r.by} <Text style={{ color: c.muted }}>{label}</Text>{' '}
        {memo ? (
          action === 'edited' ? (
            <>
              <Text style={struck}>{was.head}</Text> <Text style={{ fontWeight: '700' }}>{now.head}</Text>
            </>
          ) : (
            <>
              <Text style={{ fontWeight: '700' }}>{action}</Text>{' '}
              <Text style={action === 'deleted' ? { color: c.block } : undefined}>
                {firstLine(action === 'deleted' ? r.was : r.now).head}
              </Text>
            </>
          )
        ) : (
          <>
            <Text style={struck}>{was.head || '—'}</Text> → <Text style={{ fontWeight: '700' }}>{now.head || '—'}</Text>
            {r.field === 'state' && !r.was && r.now !== 'opened' ? <Text style={{ color: c.muted }}> · opened</Text> : null}
          </>
        )}
        {long ? <Text style={{ color: c.muted }}>{open ? ' ▾' : ' ▸'}</Text> : null}
      </Text>
      {r.note ? <Text style={{ color: c.muted, paddingLeft: space.sm }}>{firstLine(r.note, 160).head}</Text> : null}
      {open ? (
        <View testID="history-full" style={{ backgroundColor: c.panel, padding: space.sm, gap: space.xs, marginTop: space.xs }}>
          {r.was ? <Text style={struck}>{r.was}</Text> : null}
          {r.now ? <Text style={{ color: c.ink, fontWeight: '700' }}>{r.now}</Text> : null}
        </View>
      ) : null}
    </Pressable>
  );
}

function Fold({ fold }: { fold: HistoryFold }) {
  const c = usePalette();
  const [open, setOpen] = useState(false);
  const n = fold.rows.length;
  const range = fold.from === fold.to ? clockTime(fold.to) : `${clockTime(fold.from)} to ${clockTime(fold.to)}`;
  return (
    <View style={{ gap: space.xs }}>
      <Pressable
        testID="history-fold"
        onPress={() => setOpen(!open)}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        style={{ backgroundColor: c.panel, padding: space.sm }}
      >
        <Text style={{ color: c.ink }}>
          {open ? '▾ ' : '▸ '}
          <Text style={{ fontWeight: '700' }}>{n === 1 ? '1 comment change' : `${n} comment changes`}</Text> by {fold.by.join(', ')} ·{' '}
          {range}
        </Text>
      </Pressable>
      {open ? fold.rows.map((r, j) => <Change key={j} r={r} shown />) : null}
    </View>
  );
}

export function TaskHistory({ rows }: { rows: HistoryRow[] }) {
  const c = usePalette();
  const [filter, setFilter] = useState<HistoryFilter>('all');
  const [newest, setNewest] = useState(true);
  const counts = historyCounts(rows);
  const days = historyView(rows, filter, newest);
  return (
    <View testID="task-history" style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.xs }}>
        {FILTERS.map(([k, word]) => (
          <Button
            key={k}
            testID={`history-filter-${k}`}
            label={`${word} ${counts[k]}`}
            kind={filter === k ? 'yes' : 'plain'}
            onPress={() => setFilter(k)}
          />
        ))}
        <Button testID="history-order" label={newest ? 'Newest first' : 'Oldest first'} onPress={() => setNewest(!newest)} />
      </View>
      {days.map((d) => (
        <View key={d.day} style={{ gap: space.sm }}>
          <Text style={{ color: c.muted, fontSize: font.size.sm, fontWeight: '700' }}>{dayLabel(d.day)}</Text>
          {d.items.map((item, i) =>
            item.kind === 'memos' ? (
              <Fold key={`${d.day}-${i}`} fold={item} />
            ) : (
              <View key={`${d.day}-${i}`} style={{ gap: space.xs }}>
                {item.rows.map((r, j) => (
                  <Change key={j} r={r} shown={j === 0} />
                ))}
              </View>
            ),
          )}
        </View>
      ))}
    </View>
  );
}

/** The History control and, once opened, the rows. The read starts when it is opened; a daemon without task.history shows nothing. */
export function TaskHistorySection({ id }: { id: number }) {
  const { served } = useServes('task.history');
  const [open, setOpen] = useState(false);
  if (!served) return null;
  return (
    <View style={{ gap: space.sm }}>
      <Button testID="task-history-toggle" label={open ? 'Hide history' : 'History'} onPress={() => setOpen(!open)} />
      {open ? <Rows id={id} /> : null}
    </View>
  );
}

function Rows({ id }: { id: number }) {
  const c = usePalette();
  const rows = useTaskHistory(id).data?.rows ?? [];
  return rows.length ? <TaskHistory rows={rows} /> : <Text style={{ color: c.muted }}>No changes yet.</Text>;
}
