// The notices (docs: T5591 N3): the bell in the title bar with the unread count, the short results under it, and the list behind the bell.
import { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import type { Notice, Store } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { Banner, PageHeader } from '#src/screens/parts.tsx';

/** The bell and, beside it, how many notices are unread; it opens the list. */
export function Bell(props: { store: Store; onOpen: () => void }) {
  const c = usePalette();
  const unread = useSelector((s) => s.notices.unread());
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'flex-end', paddingHorizontal: space.md }}>
      <Pressable
        testID="bell"
        accessibilityRole="button"
        accessibilityLabel={unread ? `Notices, ${unread} unread` : 'Notices'}
        onPress={props.onOpen}
        hitSlop={8}
        style={{ flexDirection: 'row', alignItems: 'center', gap: space.xs, minHeight: 36 }}
      >
        <Icon name="alert" decorative size={20} color={unread ? c.ink : c.muted} />
        {unread ? (
          <Text testID="bell-count" style={{ color: c.ink, fontWeight: '700', fontSize: font.size.label }}>
            {unread}
          </Text>
        ) : null}
      </Pressable>
    </View>
  );
}

const lineOf = (n: Notice) => (n.count > 1 ? `${n.text} ×${n.count}` : n.text);

/** The results of what was just done, under the title bar: a tap closes them. Each runs out by itself. */
export function Toasts(props: { store: Store }) {
  useSelector((s) => s.version); // drawn again on every change; what is on screen depends on the clock, so it is read here and not cached
  const toasts = props.store.notices.toasts();
  const next = props.store.notices.nextExpiry();
  const [, draw] = useState(0);
  useEffect(() => {
    if (next === null) return;
    const t = setTimeout(() => draw((n) => n + 1), Math.max(next - Date.now(), 0) + 1);
    return () => clearTimeout(t);
  }, [next]);
  return (
    <View testID="toasts">
      {toasts.map((n) => (
        <Banner key={n.id} text={`${lineOf(n)}  ✕`} error={n.level === 'fail'} onPress={() => props.store.notices.dismissToasts()} />
      ))}
    </View>
  );
}

/** The list, newest first; opening it reads them all. */
export function NoticesPage(props: { store: Store; onClose: () => void }) {
  const c = usePalette();
  const list = useSelector((s) => s.notices.list());
  const [fresh] = useState(
    () =>
      new Set(
        props.store.notices
          .list()
          .filter((n) => !n.read)
          .map((n) => n.id),
      ),
  );
  useEffect(() => props.store.notices.markAllRead(), [props.store]);
  return (
    <ScrollView testID="notices-page" contentContainerStyle={{ padding: space.lg, gap: space.md }}>
      <PageHeader bare title="Notices" back={{ label: 'Back', onPress: props.onClose, testID: 'notices-close' }} />
      {list.length === 0 ? <Text style={{ color: c.muted }}>Nothing yet. Results and events show here.</Text> : null}
      {list.map((n) => (
        <View key={n.id} testID="notice" style={{ gap: 2, borderTopWidth: 1, borderTopColor: c.line, paddingTop: space.sm }}>
          <Text style={{ color: n.level === 'fail' ? c.block : c.ink, fontWeight: fresh.has(n.id) ? '700' : '400' }}>{lineOf(n)}</Text>
          <Text style={{ color: c.muted, fontSize: font.size.label }}>{new Date(n.at).toLocaleTimeString()}</Text>
        </View>
      ))}
    </ScrollView>
  );
}
