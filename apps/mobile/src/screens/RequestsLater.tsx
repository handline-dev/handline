// Later on the phone's Requests: the sheet an item's page opens (a few quick times, or when a task is done), the "Later N" fold at the foot of the
// list with when each item returns, and the Undo line a put-off leaves. The entry is the PC's: NavPrefs.later, by the item's For you key.
import { useEffect, useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { laterChoices, laterKeyOf, laterWords, requestGroup, sourceWord, type Card, type LaterAt } from '@handline/app-core';
import { useLater, useTasks } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { Button, MenuItem, MenuSheet } from '#src/screens/parts.tsx';

export type Deferred = { card: Card; at: LaterAt };

const inputDate = (ms: number) => {
  const d = new Date(ms);
  const two = (n: number) => String(n).padStart(2, '0');
  return { day: `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`, time: `${two(d.getHours())}:${two(d.getMinutes())}` };
};

/** A time of the person's own: a date and a time in two fields (the app has no native date picker), Later once both read as a time ahead. */
function PickTime(props: { now: number; onPut: (at: LaterAt) => void }) {
  const c = usePalette();
  const start = inputDate(props.now + 3_600_000);
  const [day, setDay] = useState(start.day);
  const [time, setTime] = useState(start.time);
  const when = /^\d{4}-\d\d-\d\d$/.test(day) && /^\d\d:\d\d$/.test(time) ? new Date(`${day}T${time}`).getTime() : NaN;
  const ok = Number.isFinite(when) && when > props.now;
  const field = {
    color: c.ink,
    borderWidth: 1,
    borderColor: c.line,
    borderRadius: radius.m,
    padding: space.md,
    minHeight: 48,
    fontSize: font.size.md,
  } as const;
  return (
    <View testID="later-pick" style={{ padding: space.lg, gap: space.sm }}>
      <Text style={{ color: c.muted, fontSize: font.size.sm }}>Date and time</Text>
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <TextInput
          testID="later-day"
          accessibilityLabel="Date"
          value={day}
          onChangeText={setDay}
          placeholder="2026-11-02"
          style={{ ...field, flex: 2 }}
        />
        <TextInput
          testID="later-clock"
          accessibilityLabel="Time"
          value={time}
          onChangeText={setTime}
          placeholder="09:00"
          style={{ ...field, flex: 1 }}
        />
      </View>
      <Button
        testID="later-pick-put"
        kind="yes"
        label="Later"
        disabled={!ok}
        onPress={() => props.onPut({ until: new Date(when).toISOString() })}
        style={{ minHeight: 48 }}
      />
    </View>
  );
}

/** The sheet behind an item's Later button: quick times, then a time of one's own or the open task whose end brings the item back. */
export function LaterSheet(props: { card: Card; now: number; onPut: (at: LaterAt) => void; onClose: () => void }) {
  const c = usePalette();
  const [step, setStep] = useState<'menu' | 'time' | 'task'>('menu');
  const open = (useTasks({}).data?.items ?? []).filter((t) => t.id !== props.card.task && !['done', 'discarded'].includes(t.state));
  const put = (at: LaterAt) => (props.onPut(at), props.onClose());
  return (
    <MenuSheet testID="later-sheet" onClose={props.onClose}>
      {step === 'time' ? (
        <PickTime now={props.now} onPut={put} />
      ) : step === 'task' ? (
        open.length ? (
          open.map((t) => (
            <MenuItem key={t.id} testID={`later-task-${t.id}`} label={`T${t.id} ${t.title}`} onPress={() => put({ after_task: t.id })} />
          ))
        ) : (
          <MenuItem label="No open task to wait for" disabled onPress={() => undefined} />
        )
      ) : (
        <>
          {laterChoices(props.now).map((ch, i) => (
            <MenuItem key={ch.label} testID={`later-time-${i}`} label={ch.label} onPress={() => put({ until: ch.until })} />
          ))}
          <View style={{ height: 1, backgroundColor: c.ink, opacity: 0.25 }} />
          <MenuItem testID="later-by-time" label="Pick a time…" onPress={() => setStep('time')} />
          <MenuItem testID="later-by-task" label="When a task is done…" onPress={() => setStep('task')} />
        </>
      )}
    </MenuSheet>
  );
}

/** The Later line under an item's answers: it opens the sheet. Once the item is put off it says when it returns (a tap changes that) and Return now brings it back. */
export function LaterLine(props: {
  card: Card;
  now: number;
  at: LaterAt | null;
  onPut: (card: Card, at: LaterAt | null) => void;
  /** The item was put off: the page moves on. */
  onLeave: () => void;
}) {
  const c = usePalette();
  const [sheet, setSheet] = useState(false);
  return (
    <>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <Pressable
          testID="request-later"
          accessibilityRole="button"
          onPress={() => setSheet(true)}
          style={{ flex: 1, minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: space.sm }}
        >
          <Icon name="waiting" decorative size={16} color={c.accent} />
          <Text style={{ color: c.accent }}>{props.at ? `Later: ${laterWords(props.at)}` : 'Later'}</Text>
        </Pressable>
        {props.at ? (
          <Button testID="request-return" label="Return now" onPress={() => props.onPut(props.card, null)} style={{ minHeight: 44 }} />
        ) : null}
      </View>
      {sheet ? (
        <LaterSheet
          card={props.card}
          now={props.now}
          onPut={(at) => (props.onPut(props.card, at), props.at || props.onLeave())}
          onClose={() => setSheet(false)}
        />
      ) : null}
    </>
  );
}

/** "Later N" at the foot of the list: opens in place, each row ending with when it returns. */
export function LaterFold(props: { items: Deferred[]; onOpen: (key: string) => void }) {
  const c = usePalette();
  const [open, setOpen] = useState(false);
  if (!props.items.length) return null;
  return (
    <View testID="later-fold" style={{ borderTopWidth: 1, borderTopColor: c.line }}>
      <Pressable
        testID="later-fold-head"
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        onPress={() => setOpen(!open)}
        style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, padding: space.lg, minHeight: 48 }}
      >
        <Icon name={open ? 'chevron' : 'chevronRight'} decorative size={14} color={c.muted} />
        <Text style={{ color: c.muted, fontSize: font.size.sm, fontWeight: '700' }}>{`Later · ${props.items.length}`}</Text>
      </Pressable>
      {open
        ? props.items.map(({ card, at }) => (
            <Pressable
              key={card.key}
              testID={`later-row-${card.key}`}
              accessibilityRole="button"
              accessibilityLabel={`${requestGroup(card)}: ${card.title}, back ${laterWords(at)}`}
              onPress={() => props.onOpen(card.key)}
              style={{ borderTopWidth: 1, borderTopColor: c.line, padding: space.lg, minHeight: 56, gap: space.xs }}
            >
              <Text numberOfLines={2} style={{ color: c.ink, fontWeight: '700' }}>
                {card.title}
              </Text>
              <View style={{ flexDirection: 'row', gap: space.sm }}>
                <Text style={{ color: c.muted, fontSize: font.size.sm, flex: 1 }}>{sourceWord(card)}</Text>
                <Text style={{ color: c.muted, fontSize: font.size.sm }}>{laterWords(at)}</Text>
              </View>
            </Pressable>
          ))
        : null}
    </View>
  );
}

/** Puts an item off, or returns it, at once. The line it leaves says when, and Undo puts the item back as it was. */
export function useLaterUndo(deferred: Deferred[]) {
  const setLater = useLater();
  const [undo, setUndo] = useState<{ line: string; run: () => void } | null>(null);
  useEffect(() => {
    if (!undo) return;
    const t = setTimeout(() => setUndo(null), 6000);
    return () => clearTimeout(t);
  }, [undo]);
  return {
    undo,
    runUndo: () => (undo?.run(), setUndo(null)),
    put: (card: Card, at: LaterAt | null) => {
      const key = laterKeyOf(card);
      if (!key) return;
      const before = deferred.find((l) => l.card.key === card.key)?.at ?? null;
      setLater(key, at);
      setUndo({ line: at ? `Later: ${laterWords(at)}` : 'Back in Requests', run: () => setLater(key, before) });
    },
  };
}
