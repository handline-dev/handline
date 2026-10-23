// A run of task cards drawn as one line that opens in place, and what the person has seen of each line.
import { useEffect, useRef, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { clockTime, foldTasks, type TaskFold, type TimelineEntry } from '@handline/app-core';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';

/** Which lines are open, and how many cards each held when the person last opened it; a line first seen after the chat loaded counts all its cards as new. */
export function useFolds(ready: boolean, entries: TimelineEntry[]) {
  const [open, setOpen] = useState<ReadonlySet<number>>(new Set());
  const seen = useRef(new Map<number, number>());
  const loaded = useRef(false);
  useEffect(() => {
    if (ready) loaded.current = true;
  });
  return {
    entries: foldTasks(entries),
    open: (f: TaskFold) => open.has(f.id),
    fresh: (f: TaskFold) => f.cards.length - (seen.current.get(f.id) ?? (loaded.current ? 0 : f.cards.length)),
    toggle: (f: TaskFold) => {
      seen.current.set(f.id, f.cards.length);
      setOpen((s) => new Set(s.has(f.id) ? [...s].filter((x) => x !== f.id) : [...s, f.id]));
    },
  };
}

type Folds = ReturnType<typeof useFolds>;

/** `3 tasks · 2 added · 1 done` with the time range and `N new`; a tap opens the cards in place. */
export function FoldLine(props: { f: TaskFold; folds: Folds; line: (i: TaskFold['rows'][number]['item']) => React.ReactNode }) {
  const { f, folds } = props;
  const c = usePalette();
  const open = folds.open(f);
  const fresh = folds.fresh(f);
  const range = f.at === f.until ? clockTime(f.at) : `${clockTime(f.at)}–${clockTime(f.until)}`;
  const parts = [
    `${f.tasks} ${f.tasks === 1 ? 'task' : 'tasks'}`,
    f.added ? `${f.added} added` : '',
    f.done ? `${f.done} done` : '',
  ].filter(Boolean);
  return (
    <View testID={`fold-${f.id}`} style={{ gap: space.sm }}>
      <Pressable
        testID={`fold-toggle-${f.id}`}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={open ? 'Fold the task cards' : 'Show the task cards'}
        onPress={() => folds.toggle(f)}
        style={{
          minHeight: 44,
          flexDirection: 'row',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: space.sm,
          borderWidth: 1,
          borderColor: c.line,
          borderRadius: 8,
          paddingHorizontal: space.md,
        }}
      >
        <Icon name={open ? 'less' : 'more'} decorative size={14} color={c.ink} />
        <Text style={{ color: c.ink, fontWeight: '600' }}>{parts.join(' · ')}</Text>
        <Text style={{ color: c.muted, fontSize: font.size.hint }}>{range}</Text>
        {!open && fresh > 0 ? <Text style={{ color: c.accent, fontSize: font.size.hint, fontWeight: '700' }}>{fresh} new</Text> : null}
      </Pressable>
      {open && f.rows.map((r) => <View key={r.id}>{props.line(r.item)}</View>)}
    </View>
  );
}
