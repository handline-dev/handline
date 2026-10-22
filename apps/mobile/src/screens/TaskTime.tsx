// Where a task's time went on the phone (docs/pc-app-gaps.md K5): a bar of its segments by length, the total per kind, then each segment.
import { useState } from 'react';
import { Text, View } from 'react-native';
import { clockTime, span, timeView, type TimeKind } from '@handline/app-core';
import type { TimeSegment } from '@handline/client';
import { useServes, useTaskTime } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';

const length = (seconds: number) => span(seconds * 1000);

export function TaskTime({ segments }: { segments: TimeSegment[] }) {
  const c = usePalette();
  const tone: Record<TimeKind, string> = { working: c.run, waiting: c.block, queued: c.muted, starting: c.accent };
  const { rows, totals, seconds } = timeView(segments);
  return (
    <View testID="task-time" accessibilityLabel={`${seconds} seconds in all`} style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', height: space.sm, borderRadius: space.sm, overflow: 'hidden', backgroundColor: c.panel }}>
        {rows.map((r, i) => (
          <View key={i} style={{ width: `${r.share * 100}%`, backgroundColor: tone[r.kind] }} />
        ))}
      </View>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.md }}>
        {totals.map((t) => (
          <Text key={t.kind} testID={`time-total-${t.kind}`} style={{ color: c.ink }}>
            <Text style={{ color: tone[t.kind] }}>● </Text>
            {t.word} <Text style={{ fontWeight: '700' }}>{length(t.seconds)}</Text>
          </Text>
        ))}
        <Text testID="time-total-all" style={{ color: c.muted }}>
          in all {length(seconds)}
        </Text>
      </View>
      {rows.map((r, i) => (
        <View key={i} testID={`time-segment-${r.kind}`} style={{ flexDirection: 'row', gap: space.sm }}>
          <Text style={{ color: c.muted, fontSize: font.size.label }}>{clockTime(r.from)}</Text>
          <Text style={{ color: c.ink, flex: 1 }}>{r.text}</Text>
          <Text style={{ color: c.muted }}>{length(r.seconds)}</Text>
        </View>
      ))}
    </View>
  );
}

/** The Time control and, once opened, the segments. The read starts when it is opened; a daemon without task.time shows nothing. */
export function TaskTimeSection({ id }: { id: number }) {
  const { served } = useServes('task.time');
  const [open, setOpen] = useState(false);
  if (!served) return null;
  return (
    <View style={{ gap: space.sm }}>
      <Button testID="task-time-toggle" label={open ? 'Hide time' : 'Time'} onPress={() => setOpen(!open)} />
      {open ? <Rows id={id} /> : null}
    </View>
  );
}

function Rows({ id }: { id: number }) {
  const c = usePalette();
  const segments = useTaskTime(id).data?.segments ?? [];
  return segments.length ? <TaskTime segments={segments} /> : <Text style={{ color: c.muted }}>No time yet.</Text>;
}
