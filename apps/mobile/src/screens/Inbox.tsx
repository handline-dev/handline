// The Inbox tab: what waits on the person, newest first. Reads and writes go through @handline/app-data like the web Inbox: a read item leaves
// the list at once. A decision ends its task with Done. Tap a card for its detail and verbs; a verb the daemon does not serve is disabled.
import { useState } from 'react';
import { FlatList, Pressable, Text, TextInput, View } from 'react-native';
import { ago } from '@handline/app-core';
import type { InboxItem } from '@handline/client';
import { useConnection } from '@handline/app-react';
import { useInbox, usePending, useServes, useUnread, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Button } from './parts.tsx';
import { Loadable } from './Loadable.tsx';

const KIND: Record<string, string> = {
  decision: 'Decision',
  ask: 'Question',
  say: 'Message',
  report: 'Result',
  result: 'Result',
  block: 'Block',
  prompt: 'Prompt',
};
/** A decision is the person's task: the card ends it with task.done. */
const decidable = (i: InboxItem) => i.kind === 'decision' && i.task != null;
const waits = (i: InboxItem, readPending: boolean) => !i.read && !readPending;

function useOff(method: string): string | null {
  const open = useConnection() === 'open';
  const { served } = useServes(method);
  return !open ? 'daemon not connected' : served ? null : `this daemon does not serve ${method}`;
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
  const { open, box, text } = props.ui;
  const setOpen = (v: boolean) => props.setUi({ open: v });
  const setBox = (v: Ui['box']) => props.setUi({ box: v });
  const setText = (v: string) => props.setUi({ text: v });
  const read = useWrite('inbox.mark_read');
  const done = useWrite('task.done');
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
        <Text style={{ color: c.muted }}>{i.read ? '○' : '●'}</Text>
        <View style={{ flex: 1 }}>
          <Text numberOfLines={open ? undefined : 1} style={{ color: c.ink, fontWeight: i.read ? '400' : '700' }}>
            {i.title}
          </Text>
          <Text style={{ color: c.muted, fontSize: 12 }}>
            {[KIND[i.kind] ?? i.kind, i.agent, i.task != null && `T${i.task}`].filter(Boolean).join(' · ')}
          </Text>
        </View>
        <Text style={{ color: c.muted, fontSize: 12 }}>{ago(i.created)}</Text>
      </Pressable>
      {open && (
        <View style={{ paddingHorizontal: space.lg, paddingBottom: space.lg, gap: space.md }}>
          {i.body && i.body !== i.title ? <Text style={{ color: c.ink }}>{i.body}</Text> : null}
          {i.evidence ? (
            <Text style={{ color: c.muted, fontFamily: 'Menlo', fontSize: 11 }} numberOfLines={12}>
              {i.evidence}
            </Text>
          ) : null}
          {i.prompt ? (
            <Text style={{ color: c.muted, fontFamily: 'Menlo', fontSize: 11 }}>{i.prompt.command ?? i.prompt.lines.join('\n')}</Text>
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
                style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 64 }}
              />
              <View style={{ flexDirection: 'row', gap: space.sm }}>
                <Button testID="inbox-send" kind="yes" label="Send reply" disabled={!text.trim()} onPress={send} style={{ flex: 1 }} />
                <Button label="Cancel" onPress={() => props.setUi({ box: null, text: '' })} />
              </View>
            </View>
          ) : (
            <View style={{ flexDirection: 'row', gap: space.sm, flexWrap: 'wrap' }}>
              {decidable(i) ? (
                <Button
                  testID={`inbox-done-${i.id}`}
                  label="Done"
                  disabled={!!off.decide}
                  onPress={() => {
                    // The decision ends its task, and only then is the card read: a refusal leaves it listed.
                    done.mutateAsync({ task: i.task!, evidence: 'decided in the Inbox', reason: true }).then(
                      () => read.mutate({ ids: [i.id] }),
                      () => {}, // the refusal is reported by the write itself
                    );
                  }}
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

export function Inbox() {
  const c = usePalette();
  const query = useInbox();
  const { served } = useServes('inbox.list');
  const unread = useUnread();
  const [ui, setUi] = useState<Record<number, Ui>>({});
  const markAll = useWrite('inbox.mark_read');
  const offRead = useOff('inbox.mark_read');
  const pendingRead = usePending('inbox.mark_read').flatMap((p) => p.ids);
  const pendingDone = usePending('task.done').map((p) => ('task' in p ? p.task : 0));
  const pendingReply = usePending('inbox.reply').map((p) => p.id);
  const items = (query.data?.items ?? []).filter((i) => waits(i, pendingRead.includes(i.id)));
  return (
    <View style={{ flex: 1 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', padding: space.lg, gap: space.md }}>
        <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700', flex: 1 }}>
          Inbox{' '}
          <Text testID="unread-count" style={{ color: c.muted, fontSize: 14 }}>
            {unread.length}
          </Text>
        </Text>
        <Button
          testID="mark-all-read"
          label="Mark all read"
          disabled={!unread.length || !!offRead}
          onPress={() => markAll.mutate({ ids: unread.map((i) => i.id) })}
        />
      </View>
      <Loadable
        query={query}
        served={served}
        isEmpty={items.length === 0}
        skeleton="inbox"
        label="the inbox"
        empty="Nothing waits on you. Results, questions and blocks from your agents land here."
      >
        <FlatList
          keyboardShouldPersistTaps="handled"
          data={items}
          keyExtractor={(i) => String(i.id)}
          renderItem={({ item }) => (
            <Card
              item={item}
              pending={
                pendingRead.includes(item.id) || (item.task != null && pendingDone.includes(item.task)) || pendingReply.includes(item.id)
              }
              ui={ui[item.id] ?? NO_UI}
              setUi={(patch) => setUi((all) => ({ ...all, [item.id]: { ...(all[item.id] ?? NO_UI), ...patch } }))}
              done={() => setUi(({ [item.id]: _gone, ...rest }) => rest)}
            />
          )}
        />
      </Loadable>
    </View>
  );
}
