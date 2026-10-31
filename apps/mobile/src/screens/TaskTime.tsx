// Where a task's time went on the phone (docs/pc-app-gaps.md K5): a bar of its segments by length, the total per kind, then each segment.
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { clockTime, span, timeView, type TimeLabel } from '@handline/app-core';
import type { TimeSegment } from '@handline/client';
import { useServes, useTaskTime } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';

const length = (seconds: number) => span(seconds * 1000);

const TONE = (c: ReturnType<typeof usePalette>): Record<TimeLabel, string> => ({
  working: c.run,
  founder: c.wait,
  task: c.block,
  agent: c.muted,
  account: c.block,
  waiting: c.muted,
  queued: c.muted,
  starting: c.accent,
});

export function TaskTime({ segments }: { segments: TimeSegment[] }) {
  const c = usePalette();
  const tone = TONE(c);
  const { rows, totals, seconds } = timeView(segments);
  return (
    <View testID="task-time" accessibilityLabel={`${seconds} seconds in all`} style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', height: space.sm, borderRadius: space.sm, overflow: 'hidden', backgroundColor: c.panel }}>
        {rows.map((r, i) => (
          <View key={i} style={{ width: `${r.share * 100}%`, backgroundColor: tone[r.label] }} />
        ))}
      </View>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.md }}>
        {totals.map((t) => (
          <Text key={t.label} testID={`time-total-${t.label}`} style={{ color: c.ink }}>
            <Text style={{ color: tone[t.label] }}>● </Text>
            {t.word} <Text style={{ fontWeight: '700' }}>{length(t.seconds)}</Text>
          </Text>
        ))}
        <Text testID="time-total-all" style={{ color: c.muted }}>
          in all {length(seconds)}
        </Text>
      </View>
      {rows.map((r, i) => (
        <View key={i} testID={`time-segment-${r.label}`} style={{ flexDirection: 'row', gap: space.sm }}>
          <Text style={{ color: c.muted, fontSize: font.size.sm }}>{clockTime(r.from)}</Text>
          <Text style={{ color: c.ink, flex: 1 }}>{r.text}</Text>
          <Text style={{ color: c.muted }}>{length(r.seconds)}</Text>
        </View>
      ))}
    </View>
  );
}

/** The one-line Time: "42m left", a short bar by stretch and the word for the stretch it is in; a tap opens the segments. Nothing for a daemon without task.time or a task with no stretches. */
export function TaskTimeLine({ id, left, over }: { id: number; left?: string; over?: boolean }) {
  const c = usePalette();
  const { served } = useServes('task.time');
  const [open, setOpen] = useState(false);
  const segments = useTaskTime(id).data?.segments ?? [];
  if (!served || !segments.length) return left ? <Text style={{ color: over ? c.block : c.muted }}>{left}</Text> : null;
  const { rows, totals, seconds } = timeView(segments);
  const word = totals.find((x) => x.label === rows.at(-1)?.label)?.word ?? length(seconds);
  return (
    <View style={{ gap: space.sm }}>
      <Pressable
        testID="task-time-toggle"
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={`${left ?? 'Time'}, ${length(seconds)} in all`}
        onPress={() => setOpen(!open)}
        style={{ minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: space.sm }}
      >
        {left ? <Text style={{ color: over ? c.block : c.ink }}>{left}</Text> : null}
        <View style={{ width: 80, height: 6, borderRadius: 3, overflow: 'hidden', flexDirection: 'row', backgroundColor: c.panel }}>
          {rows.map((r, i) => (
            <View key={i} style={{ width: `${r.share * 100}%`, backgroundColor: TONE(c)[r.label] }} />
          ))}
        </View>
        <Text style={{ color: c.muted, flexShrink: 1 }} numberOfLines={1}>
          {word}
        </Text>
      </Pressable>
      {open ? <TaskTime segments={segments} /> : null}
    </View>
  );
}
