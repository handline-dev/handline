// The notices (docs: T5591 N3): the bell in the title bar with the unread count, the short results under it, and the list behind the bell.
import { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { clockTime, SPARE_SHOWN, spareNotice, type Notice, type SpareNotice, type Store } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { useTasks, useWrite } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { Banner, Button, PageHeader } from '#src/screens/parts.tsx';

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
export function Toasts(props: { store: Store; onTask: (id: number) => void }) {
  useSelector((s) => s.version); // drawn again on every change; what is on screen depends on the clock, so it is read here and not cached
  const toasts = props.store.notices.toasts();
  const next = props.store.notices.nextExpiry();
  const [, draw] = useState(0);
  useEffect(() => {
    if (next === null) return;
    const t = setTimeout(() => draw((n) => n + 1), Math.max(next - Date.now(), 0) + 1);
    return () => clearTimeout(t);
  }, [next]);
  const spare = useSpareNotices();
  return (
    <View testID="toasts">
      {spare.map((n) => (
        <SpareCard key={n.task} n={n} onTask={props.onTask} />
      ))}
      {toasts.map((n) => (
        <Banner key={n.id} text={lineOf(n)} error={n.level === 'fail'} onPress={() => props.store.notices.close(n.id)} />
      ))}
    </View>
  );
}

const hueOf = (name: string) => [...name].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 360, 7);
/** The two letters after the team prefix: `handline.pc.auditor` is `au`, `handline.pc` is `pc`. */
const markOf = (agent: string) => (agent.split('.').at(-1) ?? agent).slice(0, 2).toLowerCase();

/** One notice: who did it, what it is about (the task's title), the outcome in a line, the time. A notice without a task shows its words alone. */
function NoticeRow(props: { n: Notice; fresh: boolean; onTask: (id: number) => void }) {
  const c = usePalette();
  const { n } = props;
  const { about } = n;
  const head = about?.title ?? lineOf(n);
  const sub = about ? [about.task != null ? `T${about.task} done` : 'done', about.result].filter(Boolean).join(' · ') : null;
  const task = about?.task;
  const body = (
    <>
      {props.fresh ? (
        <View testID="notice-fresh" accessibilityLabel="New" style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: c.ink }} />
      ) : null}
      {about?.agent ? (
        <View
          accessibilityLabel={about.agent}
          style={{
            width: 24,
            height: 24,
            borderRadius: 6,
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: `hsl(${hueOf(about.agent)} 45% 45%)`,
          }}
        >
          <Text style={{ color: '#fff', fontSize: 11, fontWeight: '600' }}>{markOf(about.agent)}</Text>
        </View>
      ) : null}
      <View style={{ flex: 1 }}>
        <Text numberOfLines={about ? 1 : undefined} style={{ color: n.level === 'fail' ? c.block : c.ink }}>
          {head}
        </Text>
        {sub ? (
          <Text numberOfLines={1} style={{ color: c.muted, fontSize: font.size.label }}>
            {sub}
          </Text>
        ) : null}
      </View>
      <Text style={{ color: c.muted, fontSize: font.size.label }}>{clockTime(new Date(n.at).toISOString())}</Text>
    </>
  );
  const style = {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.sm,
    borderTopWidth: 1,
    borderTopColor: c.line,
    paddingTop: space.sm,
  } as const;
  return task != null ? (
    <Pressable testID="notice" accessibilityRole="button" onPress={() => props.onTask(task)} style={style}>
      {body}
    </Pressable>
  ) : (
    <View testID="notice" style={style}>
      {body}
    </View>
  );
}

/** The list, newest first; opening it reads them all. A row for a finished task opens that task. */
export function NoticesPage(props: { store: Store; onClose: () => void; onTask: (id: number) => void }) {
  const c = usePalette();
  const list = useSelector((s) => s.notices.list());
  const spare = useSpareNotices();
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
      {spare.map((n) => (
        <SpareCard key={n.task} n={n} onTask={props.onTask} />
      ))}
      {list.length === 0 && !spare.length ? <Text style={{ color: c.muted }}>Nothing yet. Results and events show here.</Text> : null}
      {list.map((n) => (
        <NoticeRow key={n.id} n={n} fresh={fresh.has(n.id)} onTask={props.onTask} />
      ))}
    </ScrollView>
  );
}

/** The weekly-loss notices the daemon has open for the person, as task rows. */
function useSpareNotices(): SpareNotice[] {
  const items = useTasks({}).data?.items;
  return (items ?? []).flatMap((t) => spareNotice(t) ?? []);
}

/** An account will lose weekly use: the sentence, the first spare tasks, and a button that starts them all. It stays until Start or the X. */
function SpareCard(props: { n: SpareNotice; onTask: (id: number) => void }) {
  const c = usePalette();
  const { n } = props;
  const rows = new Map((useTasks({}).data?.items ?? []).map((t) => [t.id, t]));
  const answer = useWrite('task.done');
  const reply = (evidence: string) => answer.mutate({ task: n.task, evidence, reason: true });
  const small = { color: c.muted, fontSize: font.size.label } as const;
  return (
    <View testID="notice-spare" style={{ flexDirection: 'row', gap: space.sm, padding: space.md, backgroundColor: c.raised }}>
      <Icon name="waiting" decorative size={16} color={c.wait} />
      <View style={{ flex: 1, gap: space.sm }}>
        <Pressable accessibilityRole="button" onPress={() => props.onTask(n.task)}>
          <Text style={{ color: c.ink }}>{n.text}</Text>
        </Pressable>
        <View style={{ gap: 2 }}>
          {n.ids.slice(0, SPARE_SHOWN).map((id) => (
            <View key={id} style={{ flexDirection: 'row', gap: space.xs }}>
              <Text style={{ ...small, color: c.ink, fontFamily: font.mono, fontWeight: '700' }}>{`T${id}`}</Text>
              <Text numberOfLines={1} style={{ ...small, flex: 1 }}>
                {rows.get(id)?.title ?? ''}
              </Text>
              {rows.get(id) ? <Text style={small}>{rows.get(id)!.to.split('.').at(-1)}</Text> : null}
            </View>
          ))}
          {n.ids.length > SPARE_SHOWN ? <Text style={small}>{`and ${n.ids.length - SPARE_SHOWN} more`}</Text> : null}
        </View>
        {n.ids.length ? (
          <Button
            kind="yes"
            icon="play"
            label={n.ids.length === 1 ? 'Start 1 task' : `Start ${n.ids.length} tasks`}
            disabled={answer.isPending}
            onPress={() => reply('start')}
          />
        ) : null}
      </View>
      <Button
        testID="spare-close"
        icon="close"
        label=""
        accessibilityLabel="Close this notice"
        onPress={() => reply('dismiss')}
        style={{ alignSelf: 'flex-start' }}
      />
    </View>
  );
}
