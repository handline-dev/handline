// The My turn tab: what waits on the person, newest first. Reads and writes go through @handline/app-data like the web Inbox: a read item leaves
// the list at once. A decision ends its task with Done. Tap a card for its detail and verbs; a verb the daemon does not serve is disabled.
import { useState } from 'react';
import { FlatList, Pressable, Text, TextInput, View } from 'react-native';
import { ago, NOTICE_SENDER, REVIEW_TAG } from '@handline/app-core';
import type { InboxItem } from '@handline/client';
import { useStore } from '@handline/app-react';
import { useInbox, useMyTurn, useOff, usePending, useServes, useTask, useUnread, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon, type IconName } from '#src/screens/Icon.tsx';
import { Button, PageHeader } from '#src/screens/parts.tsx';
import { Loadable } from '#src/screens/Loadable.tsx';
import { PromptCard } from '#src/screens/PromptCard.tsx';
import { ReviewCard } from '#src/screens/ReviewCard.tsx';

const KIND: Record<string, string> = {
  decision: 'Decision',
  ask: 'Question',
  say: 'Message',
  report: 'Result',
  result: 'Result',
  block: 'Block',
  prompt: 'Prompt',
};
const KIND_ICON: Record<string, IconName> = {
  decision: 'decision',
  ask: 'ask',
  say: 'say',
  report: 'result',
  result: 'result',
  block: 'block',
  prompt: 'prompt',
};
/** A decision is the person's task: the card ends it with task.done. */
const decidable = (i: InboxItem) => i.kind === 'decision' && i.task != null;
const waits = (i: InboxItem, readPending: boolean) => !i.read && !readPending;

/** A decision on a task tagged review gets the review card; any other keeps Done. */
function Decide(props: { item: InboxItem; off: string | undefined; markRead: () => void }) {
  const i = props.item;
  const done = useWrite('task.done');
  const review = useTask(i.task!).data?.tags?.includes(REVIEW_TAG);
  if (review)
    return (
      <View style={{ width: '100%' }}>
        <ReviewCard task={i.task!} item={i.id} off={props.off} markRead={props.markRead} />
      </View>
    );
  return (
    <Button
      testID={`inbox-done-${i.id}`}
      label="Done"
      disabled={!!props.off}
      onPress={() => {
        // The decision ends its task, and only then is the card read: a refusal leaves it listed.
        done.mutateAsync({ task: i.task!, evidence: 'decided in the Inbox', reason: true }).then(
          props.markRead,
          () => {}, // the refusal is reported by the write itself
        );
      }}
    />
  );
}

/** What a card shows beyond the item: opened, which box, and the text typed. It lives in the list, not the card, so a refused write that
 * takes the card off the list and brings it back finds its box and its text as they were. */
interface Ui {
  open: boolean;
  box: 'reply' | null;
  text: string;
}
const NO_UI: Ui = { open: false, box: null, text: '' };

function Card(props: { item: InboxItem; pending: boolean; ui: Ui; setUi: (patch: Partial<Ui>) => void; done: () => void }) {
  const c = usePalette();
  const i = props.item;
  const store = useStore();
  const { open, box, text } = props.ui;
  const setOpen = (v: boolean) => props.setUi({ open: v });
  const setBox = (v: Ui['box']) => props.setUi({ box: v });
  const setText = (v: string) => props.setUi({ text: v });
  const read = useWrite('inbox.mark_read');
  const reply = useWrite('inbox.reply');
  const off = { read: useOff('inbox.mark_read'), decide: useOff('task.done'), reply: useOff('inbox.reply') };
  const send = () => {
    if (!text.trim()) return;
    // The box and the text stay until the daemon accepts: a refusal brings the card back with what was typed.
    reply.mutateAsync({ id: i.id, text: text.trim() }).then(props.done, () => {}); // the refusal is reported by the write itself
  };
  return (
    <View testID={`inbox-${i.id}`} style={{ borderTopWidth: 1, borderTopColor: c.line, opacity: props.pending ? 0.6 : 1 }}>
      <Pressable
        testID={`inbox-open-${i.id}`}
        onPress={() => setOpen(!open)}
        accessibilityLabel={`${i.read ? 'Read' : 'Unread'} ${KIND[i.kind] ?? i.kind}: ${i.title}`}
        style={{ flexDirection: 'row', gap: space.md, padding: space.lg, minHeight: 56, alignItems: 'center' }}
      >
        <Icon name={KIND_ICON[i.kind] ?? (i.read ? 'read' : 'unread')} decorative size={18} color={i.read ? c.muted : c.ink} />
        <View style={{ flex: 1 }}>
          <Text numberOfLines={open ? undefined : 1} style={{ color: c.ink, fontWeight: i.read ? '400' : '700' }}>
            {i.title}
          </Text>
          <Text style={{ color: c.muted, fontSize: font.size.label }}>
            {[KIND[i.kind] ?? i.kind, i.agent, i.task != null && `T${i.task}`].filter(Boolean).join(' · ')}
          </Text>
        </View>
        <Text style={{ color: c.muted, fontSize: font.size.label }}>{ago(i.created)}</Text>
      </Pressable>
      {open && (
        <View style={{ paddingHorizontal: space.lg, paddingBottom: space.lg, gap: space.md }}>
          {i.body && i.body !== i.title ? <Text style={{ color: c.ink }}>{i.body}</Text> : null}
          {i.evidence ? (
            <Text style={{ color: c.muted, fontFamily: 'Menlo', fontSize: font.size.hint }} numberOfLines={12}>
              {i.evidence}
            </Text>
          ) : null}
          {i.prompt && i.agent ? (
            <PromptCard agent={i.agent} prompt={i.prompt} />
          ) : i.prompt ? (
            <Text style={{ color: c.muted, fontFamily: 'Menlo', fontSize: font.size.hint }}>
              {i.prompt.command ?? i.prompt.lines.join('\n')}
            </Text>
          ) : null}
          {props.pending && (
            <Text testID="inbox-sending" style={{ color: c.muted }}>
              Sending…
            </Text>
          )}
          {box ? (
            <View style={{ gap: space.sm }}>
              <TextInput
                testID="inbox-text"
                autoFocus
                multiline
                value={text}
                onChangeText={setText}
                accessibilityLabel="Your reply"
                style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.card, padding: space.md, minHeight: 64 }}
              />
              <View style={{ flexDirection: 'row', gap: space.sm }}>
                <Button testID="inbox-send" kind="yes" label="Send reply" disabled={!text.trim()} onPress={send} style={{ flex: 1 }} />
                <Button label="Cancel" onPress={() => props.setUi({ box: null, text: '' })} />
              </View>
            </View>
          ) : (
            <View style={{ flexDirection: 'row', gap: space.sm, flexWrap: 'wrap' }}>
              {decidable(i) ? <Decide item={i} off={off.decide} markRead={() => read.mutate({ ids: [i.id] })} /> : null}
              {i.fix === 'open_terminal' && i.agent ? (
                <Button
                  testID={`inbox-terminal-${i.id}`}
                  label="Open terminal"
                  onPress={() => void store.openAgent(i.agent!).then(() => store.setTab('terminal'))}
                />
              ) : null}
              {i.kind !== 'decision' && !i.prompt && i.kind !== 'block' ? (
                <Button testID={`inbox-reply-${i.id}`} label="Reply…" disabled={!!off.reply} onPress={() => setBox('reply')} />
              ) : null}
              {!i.read && (
                <Button
                  testID={`inbox-read-${i.id}`}
                  label="Mark read"
                  disabled={!!off.read}
                  onPress={() => read.mutate({ ids: [i.id] })}
                />
              )}
            </View>
          )}
        </View>
      )}
    </View>
  );
}

type Entry = { key: string; item: InboxItem } | { key: 'digest' | 'read'; items: InboxItem[] };

/** The task board's messages are one row when there are several, and what is read is one row at the end: each opens to its cards. */
function entries(waiting: InboxItem[], read: InboxItem[]): Entry[] {
  const notices = waiting.filter((i) => i.agent === NOTICE_SENDER && i.kind === 'say');
  const out: Entry[] = [];
  for (const i of waiting) {
    if (notices.length < 2 || !notices.includes(i)) out.push({ key: String(i.id), item: i });
    else if (i === notices[0]) out.push({ key: 'digest', items: notices });
  }
  if (read.length) out.push({ key: 'read', items: read });
  return out;
}

/** A row that stands for several items: a line to tap, and the items' cards under it when open. */
function Group(props: { id: 'digest' | 'read'; label: string; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  const c = usePalette();
  return (
    <View testID={`inbox-group-${props.id}`} style={{ borderTopWidth: 1, borderTopColor: c.line }}>
      <Pressable
        testID={`inbox-group-open-${props.id}`}
        accessibilityRole="button"
        accessibilityState={{ expanded: props.open }}
        onPress={props.onToggle}
        style={{ padding: space.lg, minHeight: 48, justifyContent: 'center' }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <Icon name={props.open ? 'chevron' : 'chevronRight'} decorative size={16} color={c.muted} />
          <Text style={{ color: props.id === 'read' ? c.muted : c.ink, fontWeight: '600' }}>{props.label}</Text>
        </View>
      </Pressable>
      {props.open ? props.children : null}
    </View>
  );
}

export function Inbox(props: { onTask: (id: number) => void }) {
  const c = usePalette();
  const turn = useMyTurn().cards;
  const tasks = turn.filter((t) => t.kind === 'task');
  const query = useInbox();
  const { served } = useServes('inbox.list');
  const unread = useUnread();
  const [ui, setUi] = useState<Record<number, Ui>>({});
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const markAll = useWrite('inbox.mark_read');
  const offRead = useOff('inbox.mark_read');
  const pendingRead = usePending('inbox.mark_read').flatMap((p) => p.ids);
  const pendingDone = usePending('task.done').map((p) => ('task' in p ? p.task : 0));
  const pendingReply = usePending('inbox.reply').map((p) => p.id);
  const all = query.data?.items ?? [];
  const items = all.filter((i) => waits(i, pendingRead.includes(i.id)));
  const rows = entries(
    items,
    all.filter((i) => !items.includes(i)),
  );
  const card = (item: InboxItem) => (
    <Card
      key={item.id}
      item={item}
      pending={pendingRead.includes(item.id) || (item.task != null && pendingDone.includes(item.task)) || pendingReply.includes(item.id)}
      ui={ui[item.id] ?? NO_UI}
      setUi={(patch) => setUi((a) => ({ ...a, [item.id]: { ...(a[item.id] ?? NO_UI), ...patch } }))}
      done={() => setUi(({ [item.id]: _gone, ...rest }) => rest)}
    />
  );
  return (
    <View style={{ flex: 1 }}>
      <PageHeader
        title="My turn"
        actions={
          <>
            <Text testID="unread-count" style={{ color: c.muted, fontSize: font.size.body }}>
              {turn.length}
            </Text>
            <Button
              testID="mark-all-read"
              label="Mark all read"
              disabled={!unread.length || !!offRead}
              onPress={() => markAll.mutate({ ids: unread.map((i) => i.id) })}
            />
          </>
        }
      />
      <Loadable
        query={query}
        served={served}
        isEmpty={all.length === 0 && tasks.length === 0}
        skeleton="inbox"
        label="the inbox"
        empty="Nothing waits on you."
      >
        {items.length === 0 && tasks.length === 0 && (
          <Text testID="inbox-clear" style={{ color: c.muted, padding: space.lg }}>
            Nothing waits on you.
          </Text>
        )}
        <FlatList
          keyboardShouldPersistTaps="handled"
          data={rows}
          ListHeaderComponent={
            <>
              {tasks.map((t) => (
                <Pressable
                  key={t.key}
                  testID={`turn-task-${t.task}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Open T${t.task}`}
                  onPress={() => props.onTask(t.task!)}
                  style={{ padding: space.lg, minHeight: 48, borderTopWidth: 1, borderTopColor: c.line }}
                >
                  <Text style={{ color: c.muted, fontSize: font.size.hint }}>Task{t.from ? ` · from ${t.from}` : ''}</Text>
                  <Text style={{ color: c.ink, fontWeight: '600' }}>{`T${t.task} ${t.title}`}</Text>
                </Pressable>
              ))}
            </>
          }
          keyExtractor={(e) => e.key}
          renderItem={({ item: e }) =>
            'item' in e ? (
              card(e.item)
            ) : (
              <Group
                id={e.key}
                label={e.key === 'digest' ? `${NOTICE_SENDER}: ${e.items.length} messages` : `Read (${e.items.length})`}
                open={!!opened[e.key]}
                onToggle={() => setOpened((o) => ({ ...o, [e.key]: !o[e.key] }))}
              >
                {e.items.map(card)}
              </Group>
            )
          }
        />
      </Loadable>
    </View>
  );
}
