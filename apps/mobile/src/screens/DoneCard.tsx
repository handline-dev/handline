// A task the founder gave has finished: a small card floats above the tab bar for a few seconds. It overlays the page (never pushes it or covers the
// header), stays while a finger is on it, goes with a swipe, and opens the task on a tap; several at once are one "N done" card that opens the list.
// Tasks between agents, and those of the agent in view, go only to the bell list.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Animated, PanResponder, Pressable, Text, View } from 'react-native';
import type { Notice, Store } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';

export const SHOW_MS = 8000;
const SWIPE = 60;

/** A finished-task line of the list, as opposed to a stall, a proposal or a note. */
export const isTaskDone = (n: Notice): boolean =>
  n.level === 'info' && n.lane === 'team' && n.about?.task != null && n.about.proposal == null;

const short = (agent: string) => agent.split('.').at(-1) ?? agent;

export function DoneCard(props: { store: Store; agent: string | null; bottom: number; onTask: (id: number) => void; onList: () => void }) {
  const c = usePalette();
  useSelector((s) => s.version);
  const list = props.store.notices.list();
  const seen = useRef<Map<number, number> | null>(null);
  seen.current ??= new Map(list.map((n) => [n.id, n.count])); // what was already there when this screen came is not news
  const [shown, setShown] = useState<Notice[]>([]);
  const [touching, setTouching] = useState(false);
  const x = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const fresh = list.filter((n) => isTaskDone(n) && seen.current!.get(n.id) !== n.count);
    for (const n of list) seen.current!.set(n.id, n.count);
    const add = fresh.filter((n) => n.about?.fromFounder === true && n.about.agent !== props.agent);
    if (add.length) setShown((prev) => [...add, ...prev.filter((p) => !add.some((a) => a.id === p.id))]);
  });
  useEffect(() => {
    if (!shown.length || touching) return;
    const t = setTimeout(() => setShown([]), SHOW_MS);
    return () => clearTimeout(t);
  }, [shown, touching]);
  const pan = useMemo(
    () =>
      PanResponder.create({
        onMoveShouldSetPanResponder: (_, g) => Math.abs(g.dx) > 12 && Math.abs(g.dx) > Math.abs(g.dy),
        onPanResponderMove: (_, g) => x.setValue(g.dx),
        onPanResponderRelease: (_, g) => {
          x.setValue(0);
          if (Math.abs(g.dx) > SWIPE) setShown([]);
          setTouching(false);
        },
        onPanResponderTerminate: () => (x.setValue(0), setTouching(false)),
      }),
    [x],
  );
  if (!shown.length) return null;
  const one = shown.length === 1 ? shown[0]! : null;
  const open = () => {
    if (one) {
      props.store.notices.open(one.id);
      props.onTask(one.about!.task!);
    } else props.onList();
    setShown([]);
  };
  return (
    <Animated.View
      testID="done-card"
      {...pan.panHandlers}
      onTouchStart={() => setTouching(true)}
      onTouchEnd={() => setTouching(false)}
      style={{ position: 'absolute', left: space.md, right: space.md, bottom: props.bottom, transform: [{ translateX: x }] }}
    >
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={one ? `Open T${one.about!.task}` : `${shown.length} tasks done, open the list`}
        onPress={open}
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          gap: space.sm,
          minHeight: 56,
          padding: space.md,
          borderRadius: radius.card,
          borderWidth: 1,
          borderColor: c.lineStrong,
          backgroundColor: c.raised,
        }}
      >
        <Icon name="done" decorative size={20} color={c.run} />
        <View style={{ flex: 1 }}>
          <Text numberOfLines={1} style={{ color: c.ink, fontWeight: '600' }}>
            {one ? (one.about!.title ?? `T${one.about!.task} done`) : `${shown.length} tasks done`}
          </Text>
          <Text numberOfLines={1} style={{ color: c.muted, fontSize: font.size.label }}>
            {one ? `T${one.about!.task} done · ${short(one.about!.agent ?? '')}` : 'Tap to see them'}
          </Text>
        </View>
        <Text style={{ color: c.accent, fontWeight: '600' }}>{one ? 'Open' : 'See'}</Text>
      </Pressable>
    </Animated.View>
  );
}
