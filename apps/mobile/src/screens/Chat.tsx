// Chat tab of the agent page: the conversation with the agent on `useChat`, like the web's Chat. The person's messages show how far they
// got (a glyph and a word), a tap picks one to reply to, an unanswered one can be taken back, a question from the agent can be answered,
// and a refused send stays as a failed row with Retry. A message to a structured agent shows its receipt (queued, sent, confirmed, or unknown:
// lost after it was written, never sent again); while a turn runs the person picks Send now or Queue. A daemon that does not serve chat gets an empty state.
import { useEffect, useRef, useState } from 'react';
import { Alert, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { isOpenAsk, landingLabel, sideOf, timeline } from '@handline/app-core';
import { classify, sentence, useAgents, useChat, useChatActions, useInbox, useWrite, type ChatScope } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import type { AgentDetail, ChatMessage, Landing } from '@handline/client';
import { radius, space } from '@handline/tokens';
import { Skeleton } from '../skeleton.tsx';
import { usePalette } from '../theme.ts';
import { Line, clock } from './ChatLines.tsx';
import { Button, Row } from './parts.tsx';

/** A shape for each stage, so the stage does not rest on colour. */
const GLYPH: Record<string, string> = {
  queued: '…',
  typed: '⌨',
  delivered: '✓',
  delivered_unanswered: '✓?',
  answered: '✓✓',
  dismissed: '⊘',
  withdrawn: '✕',
};
const glyph = (l: Landing) => GLYPH[l] ?? '✓';

/** The receipt of a message to a structured agent, in words; unknown says what is not known. */
const RECEIPT: Record<string, string> = {
  queued: '… Queued',
  sent: '→ Sent',
  confirmed: '✓ Confirmed',
  unknown: '? May or may not have run',
};

/** Unsent text per agent, kept while the person goes to another tab or agent (not across a restart). */
const drafts = new Map<string, string>();

const snippet = (s: string) => (s.length > 80 ? `${s.slice(0, 80)}…` : s);

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

export function Chat(props: { detail: AgentDetail }) {
  const c = usePalette();
  const agent = props.detail.name;
  const canSend = useSelector((s) => s.can('chat.send'));
  const [scope, setScope] = useState<ChatScope>('mine');
  const chat = useChat(agent, scope);
  const act = useChatActions(agent);
  const [picked, setPicked] = useState<ChatMessage | null>(null);
  const [text, setTextState] = useState(drafts.get(agent) ?? '');
  const scroll = useRef<ScrollView>(null);
  const pinned = useRef(true); // the person is at the bottom, so new entries scroll into view
  const setText = (v: string) => {
    drafts.set(agent, v);
    setTextState(v);
  };
  const names = new Set((useAgents().data?.agents ?? []).map((a) => a.name));
  const entries = timeline(chat.messages, chat.items);

  // The agent's messages now in front of the person leave the FOR YOU list (chat.seen); once per newest number.
  const said = chat.messages.filter((m) => m.from === agent && m.id > 0).reduce((n, m) => Math.max(n, m.id), 0);
  const told = useRef(0);
  useEffect(() => {
    if (chat.status === 'ready' && said > told.current) {
      told.current = said;
      act.seen(said);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [said, chat.status]);

  // What the agent sent to the Inbox about itself is read once its chat is open.
  const inbox = useInbox();
  const read = useWrite('inbox.mark_read');
  const readIds = useRef(new Set<number>());
  useEffect(() => {
    if (chat.status !== 'ready') return;
    const ids = (inbox.data?.items ?? [])
      .filter((i) => i.agent === agent && !i.read && ['say', 'result', 'report'].includes(i.kind) && !readIds.current.has(i.id))
      .map((i) => i.id);
    if (!ids.length) return;
    ids.forEach((id) => readIds.current.add(id));
    read.mutate({ ids });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inbox.data, chat.status, agent]);

  if (chat.status === 'off') return <ChatEmpty />;

  const send = (delivery?: 'now' | 'queue') => {
    if (!text.trim()) return;
    // A picked message is a reference: a linked message to whoever said it, whatever it asked.
    if (picked) act.send(text, { parent: picked.id, to: names.has(picked.from) ? picked.from : picked.to, delivery });
    else act.send(text, { delivery });
    setPicked(null);
    setText('');
    pinned.current = true;
  };
  const failure = chat.status === 'error' ? sentence(classify(chat.error)) : null;
  const working = chat.working.turn_running;
  return (
    <View style={{ flex: 1 }}>
      <Row style={{ paddingHorizontal: space.md, paddingVertical: space.sm }}>
        {(['mine', 'all'] as const).map((s) => (
          <Pressable
            key={s}
            testID={`scope-${s}`}
            accessibilityRole="button"
            accessibilityState={{ selected: scope === s }}
            onPress={() => {
              setScope(s);
              setPicked(null);
            }}
            style={{
              paddingVertical: space.xs,
              paddingHorizontal: space.md,
              borderRadius: radius.md,
              borderWidth: 1,
              borderColor: scope === s ? c.accent : c.line,
            }}
          >
            <Text style={{ color: scope === s ? c.accent : c.muted, fontWeight: '600' }}>{s === 'mine' ? 'Mine' : 'All'}</Text>
          </Pressable>
        ))}
        <Text style={{ color: c.muted, fontSize: 11, flex: 1 }}>
          {scope === 'mine' ? 'You and this agent' : 'Also what agents tell each other'}
        </Text>
      </Row>
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
        {chat.status === 'ready' && !entries.length && (
          <Text testID="empty" style={{ color: c.muted, padding: space.lg }}>
            No messages yet
          </Text>
        )}
        {entries.map((e) =>
          e.kind === 'message' ? (
            <Bubble
              key={`m${e.id}`}
              m={e.message}
              agent={agent}
              isAgent={(n) => names.has(n)}
              picked={picked?.id === e.id}
              working={working}
              onPick={(m) => setPicked((p) => (p?.id === m.id ? null : m))}
              onRestore={(t) => setText(text.trim() ? `${t}\n${text}` : t)}
            />
          ) : (
            <Line key={`i${e.id}`} i={e.item} />
          ),
        )}
        {working && (
          <Text testID="working" accessibilityRole="text" style={{ color: c.muted, fontSize: 12 }}>
            ◐ {chat.working.line ?? 'Working'}…
          </Text>
        )}
      </ScrollView>
      {picked && (
        <Row style={{ paddingHorizontal: space.md, paddingTop: space.xs }}>
          <Text testID="replying" numberOfLines={1} style={{ color: c.muted, fontSize: 12, flex: 1 }}>
            ↩ #{picked.id} {snippet(picked.body)}
          </Text>
          <Pressable testID="drop-reply" accessibilityLabel="Drop the reply" hitSlop={12} onPress={() => setPicked(null)}>
            <Text style={{ color: c.muted, fontSize: 16 }}>✕</Text>
          </Pressable>
        </Row>
      )}
      <Row style={{ padding: space.sm }}>
        <TextInput
          testID="chat-input"
          value={text}
          onChangeText={setText}
          editable={canSend}
          multiline
          placeholder={canSend ? 'Send a message…' : 'This daemon does not accept chat messages'}
          placeholderTextColor={c.muted}
          style={{
            flex: 1,
            maxHeight: 120,
            color: c.ink,
            borderColor: c.line,
            borderWidth: 1,
            borderRadius: radius.md,
            padding: space.md,
          }}
        />
        {working && props.detail.structured ? (
          <View style={{ gap: space.xs }}>
            <Button testID="chat-send-now" label="Send now" disabled={!canSend || !text.trim()} onPress={() => send('now')} />
            <Button testID="chat-send" label="Queue" disabled={!canSend || !text.trim()} onPress={() => send('queue')} />
          </View>
        ) : (
          <Button testID="chat-send" label="Send" disabled={!canSend || !text.trim()} onPress={() => send()} />
        )}
      </Row>
    </View>
  );
}

function Bubble(props: {
  m: ChatMessage;
  agent: string;
  isAgent: (n: string) => boolean;
  picked: boolean;
  working: boolean;
  onPick: (m: ChatMessage) => void;
  onRestore: (text: string) => void;
}) {
  const c = usePalette();
  const { m, agent } = props;
  const act = useChatActions(agent);
  const can = useSelector((s) => ({ answer: s.can('chat.send'), dismiss: s.can('chat.dismiss') }));
  const side = sideOf(m, agent, props.isAgent);
  const [answer, setAnswer] = useState('');
  const unnumbered = m.id < 0; // shown before the daemon numbered it, or refused
  const failed = m.state === 'failed';
  const open = isOpenAsk(m, agent) && can.answer;
  const mine = side === 'person' && !unnumbered && !failed;
  const undoable = mine && ['queued', 'typed', 'delivered', 'delivered_unanswered'].includes(m.landing);
  // A sent, confirmed or unknown message is the agent's: taking it back asks first, as for a delivered one.
  const got = m.receipt ? m.receipt.state !== 'queued' : m.landing === 'delivered' || m.landing === 'delivered_unanswered';
  // The words come back to the box only once the daemon has taken the message back; a refusal leaves everything as it was.
  const takeBack = (confirm = false) =>
    act
      .cancel(m.id, confirm)
      .then(() => props.onRestore(m.body))
      .catch(() => undefined);
  const ask = () =>
    Alert.alert('Take it back?', `${agent} already has this message. It is told to stop, and what waits behind it moves up.`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Take back', style: 'destructive', onPress: () => void takeBack(true) },
    ]);
  const bg = side === 'person' ? c.panel : 'transparent';
  return (
    <Pressable
      testID={`msg-${m.id}`}
      disabled={unnumbered || failed}
      onPress={() => props.onPick(m)}
      style={{
        alignSelf: side === 'person' ? 'flex-end' : 'flex-start',
        maxWidth: '88%',
        gap: 4,
        padding: space.md,
        borderRadius: radius.lg,
        backgroundColor: bg,
        borderWidth: 1,
        borderColor: props.picked ? c.accent : failed ? c.block : c.line,
        opacity: unnumbered && !failed ? 0.6 : 1,
      }}
    >
      {side === 'other' && (
        <Text style={{ color: c.muted, fontSize: 11 }}>
          {m.from} → {m.to}
        </Text>
      )}
      {m.parent != null && <Text style={{ color: c.muted, fontSize: 11 }}>↩ #{m.parent}</Text>}
      <Text testID={`body-${m.id}`} style={{ color: c.ink }}>
        {m.body}
      </Text>
      <Text style={{ color: c.muted, fontSize: 11 }}>
        {!unnumbered && `#${m.id} `}
        {clock(m.at)}
        {side === 'person' && !failed && (
          <Text testID={`landing-${m.id}`}>
            {'  '}
            {m.receipt ? (RECEIPT[m.receipt.state] ?? RECEIPT.sent) : `${glyph(m.landing)} ${landingLabel(m.landing)}`}
          </Text>
        )}
        {open && '  ? Waiting for you'}
        {m.tasks.map((t) => `  T${t.id}`).join('')}
      </Text>
      {failed && (
        <View accessibilityRole="alert" style={{ gap: space.xs }}>
          <Text style={{ color: c.block, fontSize: 12 }}>✕ {m.sending_reason}</Text>
          <Row>
            <Button testID={`retry-${m.id}`} label="Retry" onPress={() => act.retry(m.id)} />
            <Button testID={`discard-${m.id}`} label="Discard" kind="no" onPress={() => act.discard(m.id)} />
          </Row>
        </View>
      )}
      {mine && m.receipt?.state === 'unknown' && (
        <Text testID={`unknown-${m.id}`} style={{ color: c.block, fontSize: 12 }}>
          Lost after it was written. It may or may not have run, and it is not sent again. Send it as a new message if you want it repeated.
        </Text>
      )}
      {undoable && (
        <Row>
          {(m.receipt ? m.receipt.state === 'queued' : m.landing === 'queued' && props.working) && (
            <Button testID={`now-${m.id}`} label="Send now" onPress={() => act.sendNow(m.id)} />
          )}
          <Button testID={`takeback-${m.id}`} label="Take back" onPress={() => (got ? ask() : void takeBack())} />
        </Row>
      )}
      {side === 'agent' && m.answer && <Text style={{ color: c.muted, fontSize: 12 }}>✓✓ You: {m.answer.text}</Text>}
      {open && (
        <View style={{ gap: space.xs }}>
          <TextInput
            testID={`answer-input-${m.id}`}
            value={answer}
            onChangeText={setAnswer}
            placeholder="Answer"
            placeholderTextColor={c.muted}
            style={{ color: c.ink, borderColor: c.line, borderWidth: 1, borderRadius: radius.md, padding: space.sm }}
          />
          <Row>
            <Button
              testID={`answer-send-${m.id}`}
              label="Answer"
              disabled={!answer.trim()}
              onPress={() => {
                act.send(answer, { parent: m.id, to: props.isAgent(m.from) ? m.from : m.to });
                setAnswer('');
              }}
            />
            {can.dismiss && <Button testID={`dismiss-${m.id}`} label="Dismiss" onPress={() => act.dismiss(m.id)} />}
          </Row>
        </View>
      )}
    </Pressable>
  );
}
