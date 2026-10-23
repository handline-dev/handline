// Chat tab of the agent page: the conversation with the agent on `useChat`, like the web's Chat. The person's messages show how far they
// got (a glyph and a word), a tap picks one to reply to, an undelivered one can be taken back,
// and a refused send stays as a failed row with Retry. A message to a structured agent shows its receipt (queued, sent, confirmed, or unknown:
// lost after it was written, never sent again); while a turn runs the person picks Send now or Queue. A search hit lands on its message. A daemon that does not serve chat gets an empty state.
import { useEffect, useRef, useState } from 'react';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { chatEntries, clockTime, decidedProposals, ellipsis, sideOf, tickLevel } from '@handline/app-core';
import { classify, sentence, useChat, useChatActions, useChatReadMarks, useHr, useServes, useTasks } from '@handline/app-data';
import { useSelector, useStore } from '@handline/app-react';
import type { AgentDetail, ChatMessage, TranscriptItem } from '@handline/client';
import { font, radius, space } from '@handline/tokens';
import { Skeleton } from '#src/skeleton.tsx';
import { usePalette } from '#src/theme.ts';
import { Decided, Line, Stamp, useBubbleWidth } from '#src/screens/ChatLines.tsx';
import { Pictures } from '#src/screens/ChatPictures.tsx';
import { ChatActions } from '#src/screens/ChatActions.tsx';
import { PrBody } from '#src/screens/PrBody.tsx';
import { ChatFind } from '#src/screens/ChatFind.tsx';
import { ChatRefs, type Ref } from '#src/screens/ChatRefs.tsx';
import { HandoffLine } from '#src/screens/HandoffLine.tsx';
import { taskTab, useOpenInTab } from '#src/screens/AgentTabs.tsx';
import { NeedsYouCard } from '#src/screens/NeedsYouCard.tsx';
import { ChatSearch } from '#src/screens/ChatSearch.tsx';
import { useChatImages } from '#src/screens/ChatImages.tsx';
import { FoldLine, useFolds } from '#src/screens/ChatFold.tsx';
import { SendButton, WorkingBubble } from '#src/screens/ChatSend.tsx';
import { Icon } from '#src/screens/Icon.tsx';
import { Button, EmptyState, Link, Row } from '#src/screens/parts.tsx';

/** How far the person's message got, as ticks: not sent yet, sent, read. */
const TICKS = ['queued', 'delivered', 'answered'] as const;

/** Unsent text per agent, kept while the person goes to another tab or agent (not across a restart). */
const drafts = new Map<string, string>();

/** The line over the composer saying what the next message replies to, with a button to drop it. */
function ReplyRow({ id, dropId, text, onDrop }: { id: string; dropId: string; text: string; onDrop: () => void }) {
  const c = usePalette();
  return (
    <Row style={{ paddingHorizontal: space.md, paddingTop: space.xs }}>
      <Icon name="reference" decorative size={14} color={c.muted} />
      <Text testID={id} numberOfLines={1} style={{ color: c.muted, fontSize: font.size.label, flex: 1 }}>
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
      <Text style={{ color: c.muted }}>This daemon does not serve chat. Use the Terminal tab to talk to the agent.</Text>
    </View>
  );
}

export function Chat(props: { detail: AgentDetail; searching: boolean }) {
  const c = usePalette();
  const agent = props.detail.name;
  const canSend = useSelector((s) => s.can('chat.send'));
  const chat = useChat(agent, 'mine');
  const act = useChatActions(agent);
  const [picked, setPicked] = useState<Ref | null>(null); // a message, or a request a # chose
  const img = useChatImages(canSend);
  const [item, setItem] = useState<TranscriptItem | null>(null); // one of the agent's replies; never together with a picked message
  // A reply item is an id of the transcript, so a daemon that serves no transcript has nothing to answer.
  const canReplyItem = useSelector((s) => !!s.client.hello?.capabilities.transcript);
  const [text, setTextState] = useState(drafts.get(agent) ?? '');
  const tabs = useOpenInTab();
  const setOpenTask = (id: number) => tabs?.open(taskTab(id)); // a task a batch card opened is a tab beside the chat
  const scroll = useRef<ScrollView>(null);
  const pinned = useRef(true); // the person is at the bottom, so new entries scroll into view
  const [going, setGoing] = useState<number | null>(null); // a message number being found
  const [flash, setFlash] = useState<number | null>(null); // the message a search hit just landed on
  const rowY = useRef(new Map<number, number>());
  const [missing, setMissing] = useState(false); // the hit's message could not be found
  const setText = (v: string) => {
    drafts.set(agent, v);
    setTextState(v);
  };
  const hr = useHr({});
  const me = useStore().me;
  const { served } = useServes('tasks.list');
  const tasks = useTasks({ all: true, limit: 200 }); // done tasks too: a card of a closed task keeps its title
  const titles = new Map((served ? (tasks.data?.items ?? []) : []).map((t) => [t.id, t.title]));
  const entries = chatEntries(chat.messages, chat.items, agent, decidedProposals(hr.data?.proposals, agent));
  const folds = useFolds(chat.status === 'ready', entries);

  useChatReadMarks(agent, chat, act.seen);

  const drawn = going !== null && entries.some((e) => e.kind !== 'item' && e.id === going);
  const reveal = (id: number) => {
    const y = rowY.current.get(id);
    if (y !== undefined) scroll.current?.scrollTo({ y: Math.max(0, y - space.lg), animated: true });
  };
  const found = () => {
    pinned.current = false;
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
    if (empty) return;
    const out = img.finish(text);
    const { text: words, attachments } = out instanceof Promise ? await out : out; // a marked picture is drawn and uploaded first
    // A picked message is only a reference: the message goes to this chat's agent, whoever said the picked one.
    if (picked) act.send(words, { parent: picked.id, delivery, attachments });
    else if (item) act.send(words, { parent_item: item.id, delivery, attachments });
    else act.send(words, { delivery, attachments });
    img.clear();
    setPicked(null);
    setItem(null);
    setText('');
    pinned.current = true;
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
        contentContainerStyle={{ padding: space.sm, gap: space.sm }}
        keyboardShouldPersistTaps="handled"
        scrollEventThrottle={64}
        onScroll={(e) => {
          const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
          pinned.current = contentSize.height - contentOffset.y - layoutMeasurement.height < 40;
          chat.holdPlace(!pinned.current);
          if (contentOffset.y < 60 && chat.hasOlder && !chat.loadingOlder) chat.loadOlder();
        }}
        onContentSizeChange={() => pinned.current && scroll.current?.scrollToEnd({ animated: false })}
      >
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
              }}
              working={working}
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
        <WorkingBubble working={chat.working} messages={chat.messages} items={chat.items} />
      </ScrollView>
      <HandoffLine handoff={props.detail.handoff ?? null} items={chat.items} />
      <NeedsYouCard agent={agent} needs={props.detail.needs_you ?? null} prompt={props.detail.prompt ?? null} />
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
      {img.chips}
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
            color: c.ink,
            borderColor: c.line,
            borderWidth: 1,
            borderRadius: radius.card,
            padding: space.md,
          }}
        />
        <SendButton disabled={!canSend || empty} choose={!!working && !!props.detail.structured} onSend={(d) => void send(d)} />
      </Row>
    </View>
  );
}

function Bubble(props: {
  m: ChatMessage;
  agent: string;
  picked: boolean;
  flashed: boolean;
  onRow: (y: number) => void;
  working: boolean;
  onPick: (m: ChatMessage) => void;
}) {
  const c = usePalette();
  const bubble = useBubbleWidth();
  const { m, agent } = props;
  const act = useChatActions(agent);
  const side = sideOf(m, agent, useStore().me);
  const unnumbered = m.id < 0; // shown before the daemon numbered it, or refused
  const failed = m.state === 'failed';
  const mine = side === 'person' && !unnumbered && !failed;
  const bg = side === 'person' ? c.panel : 'transparent';
  const stamp = (
    <Stamp
      m={m}
      time={props.picked ? clockTime(m.at) : null}
      ticks={
        side === 'person' && !failed
          ? m.landing === 'withdrawn'
            ? 'withdrawn'
            : m.receipt?.state === 'unknown'
              ? 'maybeRan'
              : TICKS[tickLevel(m)]
          : null
      }
    />
  );
  // Like the PC chat: the ticks sit beside the bubble at its bottom, and the time shows once the row is picked (PC shows it on hover).
  return (
    <Pressable
      testID={`msg-${m.id}`}
      disabled={unnumbered || failed}
      onLayout={(e) => props.onRow(e.nativeEvent.layout.y)}
      onPress={() => props.onPick(m)}
      style={{ flexDirection: 'row', alignItems: 'flex-end', justifyContent: side === 'person' ? 'flex-end' : 'flex-start', gap: space.xs }}
    >
      {side === 'person' && stamp}
      <View
        style={{
          flexShrink: 1,
          maxWidth: bubble,
          gap: 4,
          padding: space.md,
          borderRadius: radius.dialog,
          backgroundColor: bg,
          borderWidth: 1,
          borderColor: props.picked || props.flashed ? c.accent : failed ? c.block : c.line,
          opacity: unnumbered && !failed ? 0.6 : 1,
        }}
      >
        {props.flashed && (
          <Text testID={`found-${m.id}`} style={{ color: c.accent, fontSize: font.size.hint }}>
            Found by search
          </Text>
        )}
        {side === 'other' && (
          <Text style={{ color: c.muted, fontSize: font.size.hint }}>
            {m.from} → {m.to}
          </Text>
        )}
        {m.parent != null && <Text style={{ color: c.muted, fontSize: font.size.hint }}>↩ reply</Text>}
        {m.parent_item && (
          <Text testID={`quote-${m.id}`} numberOfLines={2} style={{ color: c.muted, fontSize: font.size.hint }}>
            ↩ {m.parent_item.text.replace(/\s+/g, ' ')}
          </Text>
        )}
        {m.body ? <PrBody id={m.id} agent={agent} body={m.body} /> : null}
        {m.attachments?.length ? <Pictures items={m.attachments} /> : null}
        {m.actions?.length ? <ChatActions id={m.id} actions={m.actions} /> : null}
        {failed && (
          <View accessibilityRole="alert" style={{ gap: space.xs }}>
            <Row>
              <Icon name="failed" decorative size={14} color={c.block} />
              <Text style={{ color: c.block, fontSize: font.size.label, flex: 1 }}>{m.sending_reason}</Text>
            </Row>
            <Row>
              <Button testID={`retry-${m.id}`} label="Retry" onPress={() => act.retry(m.id)} />
              <Button testID={`discard-${m.id}`} label="Discard" kind="no" onPress={() => act.discard(m.id)} />
            </Row>
          </View>
        )}
        {mine && m.receipt?.state === 'unknown' && (
          <Text testID={`unknown-${m.id}`} style={{ color: c.block, fontSize: font.size.label }}>
            Lost after it was written. It may or may not have run, and it is not sent again. Send it as a new message if you want it
            repeated.
          </Text>
        )}
        {mine && (m.receipt ? m.receipt.state === 'queued' : m.landing === 'queued' && props.working) && (
          <Row style={{ gap: space.lg }}>
            <Link testID={`now-${m.id}`} label="Send now" onPress={() => act.sendNow(m.id)} />
          </Row>
        )}
      </View>
      {side !== 'person' && stamp}
    </Pressable>
  );
}
