// Later on the phone's Requests: the sheet an item's page opens (a few quick times, or when a task is done), the "Later N" fold at the foot of the
// list with when each item returns, and the Undo line a put-off leaves. The entry is the PC's: NavPrefs.later, by the item's For you key.
import { useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { laterChoices, laterKeyOf, laterWords, requestGroup, sourceWord, type Card, type LaterAt } from '@handline/app-core';
import { useLater, useTasks } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { MenuItem, MenuSheet } from '#src/screens/parts.tsx';

export type Deferred = { card: Card; at: LaterAt };

/** The sheet behind an item's Later button: quick times, or the open task whose end brings the item back. */
export function LaterSheet(props: { card: Card; now: number; onPut: (at: LaterAt) => void; onClose: () => void }) {
  const [byTask, setByTask] = useState(false);
  const open = (useTasks({}).data?.items ?? []).filter((t) => t.id !== props.card.task && !['done', 'discarded'].includes(t.state));
  const put = (at: LaterAt) => (props.onPut(at), props.onClose());
  return (
    <MenuSheet testID="later-sheet" onClose={props.onClose}>
      {byTask ? (
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
          <MenuItem testID="later-by-task" label="When a task is done…" onPress={() => setByTask(true)} />
        </>
      )}
    </MenuSheet>
  );
}

/** The Later line under an item's answers: it opens the sheet; once the item is put off it says when it returns and brings it back at once. */
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
      <Pressable
        testID="request-later"
        accessibilityRole="button"
        onPress={() => (props.at ? props.onPut(props.card, null) : setSheet(true))}
        style={{ minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: space.sm }}
      >
        <Icon name="waiting" decorative size={16} color={c.accent} />
        <Text style={{ color: c.accent }}>{props.at ? `Later: ${laterWords(props.at)}. Back to Requests now` : 'Later'}</Text>
      </Pressable>
      {sheet ? (
        <LaterSheet
          card={props.card}
          now={props.now}
          onPut={(at) => (props.onPut(props.card, at), props.onLeave())}
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
