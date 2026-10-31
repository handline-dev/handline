// Chat tab of the agent page: the conversation with the agent on `useChat`, like the web's Chat. The person's messages show how far they
// got (a glyph and a word), a tap picks one to reply to, an undelivered one can be taken back,
// and a refused send stays as a failed row with Retry. A message to a structured agent shows its receipt (queued, sent, confirmed, or unknown:
// lost after it was written, never sent again); while a turn runs the person picks Send now or Queue. A search hit lands on its message. A daemon that does not serve chat gets an empty state.
import { useEffect, useRef, useState } from 'react';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { chatEntries, decidedProposals, ellipsis, sideOf } from '@handline/app-core';
import { classify, sentence, useChat, useChatActions, useChatReadMarks, useHr, useServes, useTasks, useWrite } from '@handline/app-data';
import { useSelector, useStore } from '@handline/app-react';
import type { AgentDetail, TranscriptItem } from '@handline/client';
import { font, radius, space } from '@handline/tokens';
import { Skeleton } from '#src/skeleton.tsx';
import { usePalette } from '#src/theme.ts';
import { Decided, Line } from '#src/screens/ChatLines.tsx';
import { AgentStrip } from '#src/screens/AgentStrip.tsx';
import { ChatFind } from '#src/screens/ChatFind.tsx';
import { ChatRefs, type Ref } from '#src/screens/ChatRefs.tsx';
import { HandoffLine } from '#src/screens/HandoffLine.tsx';
import { taskTab, useOpenInTab } from '#src/screens/AgentTabs.tsx';
import { NeedsYouCard } from '#src/screens/NeedsYouCard.tsx';
import { ChatSearch } from '#src/screens/ChatSearch.tsx';
import { useChatImages } from '#src/screens/ChatImages.tsx';
import { FoldLine, useFolds } from '#src/screens/ChatFold.tsx';
import { useChatScroll } from '#src/screens/ChatScroll.ts';
import { Bubble } from '#src/screens/ChatBubble.tsx';
import { SendButton, WorkingLine } from '#src/screens/ChatSend.tsx';
import { Icon } from '#src/screens/Icon.tsx';
import { Button, EmptyState, Row } from '#src/screens/parts.tsx';

/** Unsent text per agent, kept while the person goes to another tab or agent (not across a restart). */
const drafts = new Map<string, string>();

/** The line over the composer saying what the next message replies to, with a button to drop it. */
function ReplyRow({ id, dropId, text, onDrop }: { id: string; dropId: string; text: string; onDrop: () => void }) {
  const c = usePalette();
  return (
    <Row style={{ paddingHorizontal: space.md, paddingTop: space.xs }}>
      <Icon name="reference" decorative size={14} color={c.muted} />
      <Text testID={id} numberOfLines={1} style={{ color: c.muted, fontSize: font.size.sm, flex: 1 }}>
        {text}
      </Text>
      <Pressable testID={dropId} accessibilityLabel="Drop the reply" hitSlop={12} onPress={onDrop}>
        <Icon name="close" decorative size={18} color={c.muted} />
      </Pressable>
    </Row>
  );
}

export function ChatEmpty() {
  const c = usePalette();
  return (
    <View style={{ padding: space.xl, gap: space.sm }}>
      <Text testID="chat-off" style={{ color: c.ink, fontWeight: '600' }}>
        Chat is not available yet
      </Text>
      <Text style={{ color: c.muted }}>This needs a newer Handline. Use the Terminal tab to talk to the agent.</Text>
    </View>
  );
}

export function Chat(props: { detail: AgentDetail; searching: boolean }) {
  const c = usePalette();
  const agent = props.detail.name;
  const canSend = useSelector((s) => s.can('chat.send'));
  const chat = useChat(agent, 'mine');
  const act = useChatActions(agent);
  const interrupt = useWrite('session.interrupt', agent);
  const [picked, setPicked] = useState<Ref | null>(null); // a message, or a request a # chose
  const [item, setItem] = useState<TranscriptItem | null>(null); // one of the agent's replies; never together with a picked message
  // A reply item is an id of the transcript, so a daemon that serves no transcript has nothing to answer.
  const canReplyItem = useSelector((s) => !!s.client.hello?.capabilities.transcript);
  const sending = useRef(false);
  const [text, setTextState] = useState(drafts.get(agent) ?? '');
  const tabs = useOpenInTab();
  const setOpenTask = (id: number) => tabs?.open(taskTab(id)); // a task a batch card opened is a tab beside the chat
  const scroll = useRef<ScrollView>(null);
  const [going, setGoing] = useState<number | null>(null); // a message number being found
  const [flash, setFlash] = useState<number | null>(null); // the message a search hit just landed on
  const rowY = useRef(new Map<number, number>());
  const [missing, setMissing] = useState(false); // the hit's message could not be found
  const setText = (v: string) => {
    drafts.set(agent, v);
    setTextState(v);
  };
  const cmd = text.startsWith('!'); // command mode, as on the PC: sent as typed, the box shows it with the colour and mono type
  const img = useChatImages(canSend && !cmd, agent);
  const hr = useHr({});
  const me = useStore().me;
  const { served } = useServes('tasks.list');
  const tasks = useTasks({ all: true, limit: 200 }); // done tasks too: a card of a closed task keeps its title
  const titles = new Map((served ? (tasks.data?.items ?? []) : []).map((t) => [t.id, t.title]));
  const entries = chatEntries(chat.messages, chat.items, agent, decidedProposals(hr.data?.proposals, agent));
  const folds = useFolds(chat.status === 'ready', entries);
  const sent = entries.findLast((e) => e.kind === 'message' && sideOf(e.message, agent, me) === 'person')?.id; // the row a send holds at the top
  const anchor = useChatScroll(scroll, { ready: chat.status === 'ready', sent });

  useChatReadMarks(agent, chat);

  const drawn = going !== null && entries.some((e) => e.kind !== 'item' && e.id === going);
  const reveal = (id: number) => {
    const y = rowY.current.get(id);
    if (y !== undefined) scroll.current?.scrollTo({ y: Math.max(0, y + space.sm - space.lg), animated: true }); // y is in the lines' coordinates, which start under the padding
  };
  const found = () => {
    anchor.release();
    setFlash(going);
    if (going !== null) reveal(going);
    setGoing(null);
  };
  const lost = () => {
    setGoing(null);
    setMissing(true);
  };
  useEffect(() => {
    if (flash === null) return;
    const t = setTimeout(() => setFlash(null), 1500);
    return () => clearTimeout(t);
  }, [flash]);

  if (chat.status === 'off') return <ChatEmpty />;

  // Words or pictures to send, and none still on its way up.
  const empty = (!text.trim() && !img.ids.length) || img.busy;
  const send = async (delivery?: 'now' | 'queue') => {
    if (empty || sending.current) return; // a second press while the pictures are still being uploaded is the same send
    sending.current = true;
    const out = img.finish(text);
    let sent: Awaited<ReturnType<typeof img.finish>>;
    try {
      sent = out instanceof Promise ? await out : out; // a marked picture is drawn and uploaded first
    } finally {
      sending.current = false;
    }
    const { text: words, attachments } = sent;
    // A picked message is only a reference: the message goes to this chat's agent, whoever said the picked one.
    // A command is sent as typed: the reply selection is dropped, so nothing is put before the `!`.
    if (cmd) act.send(words, { delivery, attachments });
    else if (picked) act.send(words, { parent: picked.id, delivery, attachments });
    else if (item) act.send(words, { parent_item: item.id, delivery, attachments });
    else act.send(words, { delivery, attachments });
    img.clear();
    setPicked(null);
    setItem(null);
    setText('');
    anchor.start();
  };
  const failure = chat.status === 'error' ? sentence(classify(chat.error)) : null;
  const working = chat.working.turn_running;
  return (
    <View style={{ flex: 1 }}>
      {props.searching && (
        <ChatSearch
          agent={agent}
          onGo={(id) => {
            setMissing(false);
            setGoing(id);
          }}
        />
      )}
      {going !== null && <ChatFind id={going} chat={chat} drawn={drawn} onFound={found} onLost={lost} />}
      {missing && (
        <Text testID="search-lost" style={{ color: c.muted, paddingHorizontal: space.md }}>
          That message is not in this chat.
        </Text>
      )}
      <ScrollView
        ref={scroll}
        testID="chat"
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: space.sm, gap: space.sm, flexGrow: 1, justifyContent: 'flex-end' }}
        keyboardShouldPersistTaps="handled"
        scrollEventThrottle={64}
        onLayout={(e) => anchor.layout(e.nativeEvent.layout.height)}
        onScrollBeginDrag={anchor.drag}
        onScroll={(e) => {
          const { contentOffset } = e.nativeEvent;
          chat.holdPlace(!anchor.scrolled(e));
          if (contentOffset.y < 60 && chat.hasOlder && !chat.loadingOlder) chat.loadOlder();
        }}
      >
        <View style={{ gap: space.sm }} onLayout={(e) => anchor.lines(e.nativeEvent.layout.height)}>
          <Skeleton shape="chat" loading={chat.status === 'loading'} label="the conversation" />
          {failure && (
            <View testID="error" accessibilityRole="alert" style={{ gap: space.sm }}>
              <Text style={{ color: c.block }}>{failure}</Text>
              <Button label="Retry" onPress={chat.refetch} />
            </View>
          )}
          {chat.hasOlder && <Button testID="older" label="Older messages" disabled={chat.loadingOlder} onPress={chat.loadOlder} />}
          {chat.status === 'ready' && !entries.length && <EmptyState icon="chat" text="No messages yet" />}
          {folds.entries.map((e) =>
            e.kind === 'fold' ? (
              <FoldLine
                key={`f${e.id}`}
                f={e}
                folds={folds}
                line={(i) => <Line i={i} agent={agent} me={me} titles={titles} onTask={setOpenTask} />}
              />
            ) : e.kind === 'proposal' ? (
              <Decided key={`p${e.id}`} p={e.proposal} />
            ) : e.kind !== 'item' ? (
              <Bubble
                key={`m${e.id}`}
                m={e.message}
                agent={agent}
                picked={picked?.id === e.id}
                flashed={flash === e.id}
                onRow={(y) => {
                  rowY.current.set(e.id, y);
                  if (flash === e.id) reveal(e.id);
                  anchor.row(e.id, y);
                }}
                working={working}
                onTask={setOpenTask}
                onPick={(m) => {
                  setItem(null);
                  setPicked((p) => (p?.id === m.id ? null : m));
                }}
              />
            ) : (
              <Line
                key={`i${e.id}`}
                i={e.item}
                agent={agent}
                me={me}
                titles={titles}
                onTask={setOpenTask}
                onReply={
                  canSend && canReplyItem
                    ? (i) => {
                        setPicked(null);
                        setItem((p) => (p?.id === i.id ? null : i));
                      }
                    : undefined
                }
              />
            ),
          )}
          <NeedsYouCard agent={agent} needs={props.detail.needs_you ?? null} prompt={props.detail.prompt ?? null} />
        </View>
        {anchor.room > 0 && <View testID="send-spacer" style={{ height: anchor.room }} onLayout={anchor.spacer} />}
      </ScrollView>
      <HandoffLine handoff={props.detail.handoff ?? null} items={chat.items} />
      {picked && <ReplyRow id="replying" dropId="drop-reply" text={ellipsis(picked.body, 80)} onDrop={() => setPicked(null)} />}
      {item && (
        <ReplyRow
          id="replying-item"
          dropId="drop-reply-item"
          text={ellipsis((item.text ?? '').replace(/\s+/g, ' '), 80)}
          onDrop={() => setItem(null)}
        />
      )}
      {canSend && <ChatRefs agent={agent} text={text} onPick={(r, rest) => (setItem(null), setPicked(r), setText(rest))} />}
      <WorkingLine working={chat.working} messages={chat.messages} items={chat.items} onStop={() => interrupt.mutate({ agent })} />
      {img.chips}
      {cmd && (
        <Text testID="cmd-hint" style={{ color: c.muted, fontSize: font.size.xs, paddingHorizontal: space.md }}>
          Sent as typed to the agent
        </Text>
      )}
      <Row style={{ padding: space.sm }}>
        {img.button}
        <TextInput
          testID="chat-input"
          value={text}
          onChangeText={setText}
          editable={canSend}
          multiline
          placeholder={canSend ? 'Message…' : 'This daemon does not accept chat messages'}
          placeholderTextColor={c.muted}
          style={{
            flex: 1,
            maxHeight: 120,
            color: cmd ? c.cmd : c.ink,
            borderColor: cmd ? c.cmd : c.line,
            borderWidth: cmd ? 2 : 1,
            borderRadius: radius.m,
            padding: space.md,
            ...(cmd ? { fontFamily: 'Menlo' } : {}),
          }}
        />
        <SendButton
          disabled={!canSend || empty}
          color={cmd ? c.cmd : undefined}
          choose={!!working && !!props.detail.structured}
          onSend={(d) => void send(d)}
        />
      </Row>
      <AgentStrip detail={props.detail} />
    </View>
  );
}
