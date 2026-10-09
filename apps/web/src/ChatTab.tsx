// Chat tab: the conversation with the agent. The person's messages show how far they got (sending, delivered, not answered...), the agent's
// replies, tool lines and task events follow in time, and a question from the agent can be answered or dismissed here.
// A daemon that does not serve chat gets an empty state; the permission card shows either way.
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { AgentDetail, ChatMessage } from '@handline/client';
import { timeline } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import {
  Skeleton,
  classify,
  sentence,
  useChat,
  useChatActions,
  useChatLocate,
  useInbox,
  useWrite,
  type ChatScope,
} from '@handline/app-data';
import { AnimatedItem, AnimatedList } from './AnimatedList.tsx';
import { PromptSlot } from './PromptCard.tsx';
import { Compose } from './ChatCompose.tsx';
import { Item, Message, jumpTo } from './ChatMessages.tsx';
import { ChatBanner, ChatStrip, HrCards } from './ChatCards.tsx';
import { Button, Empty, Icon, Segmented } from './ui/index.ts';
import { useWorkspace } from './Workspace.tsx';

function ChatOff({ agent, detail }: { agent: string; detail: AgentDetail }) {
  return (
    <section className="chat">
      <div className="stream">
        <Empty
          icon="chat"
          name="No chat yet"
          hint={detail.structured ? 'Send a message to start' : 'Use the terminal tab to work with the agent'}
        />
      </div>
      <div className="cards">
        <PromptSlot agent={agent} prompt={detail.prompt} />
      </div>
      <div className="compose-row">
        <textarea
          className="compose"
          rows={2}
          placeholder="Chat is not available from this daemon yet"
          disabled
          aria-label="Message to the agent"
        />
        <Button variant="solid" icon="send" label="Send" disabled />
      </div>
    </section>
  );
}

export function ChatTab({ agent, detail }: { agent: string; detail: AgentDetail }) {
  const { storage } = useWorkspace();
  const canSend = useSelector((s) => s.can('chat.send'));
  const [scope, setScope] = useState<ChatScope>('mine');
  const chat = useChat(agent, scope);
  const [picked, setPicked] = useState<number | null>(null);
  const [restore, setRestore] = useState<{ text: string; n: number } | null>(null);
  const [newer, setNewer] = useState(0); // entries that arrived while the person was reading further up
  const [going, setGoing] = useState<number | null>(null); // a message number being found
  const act = useChatActions(agent);
  const locate = useChatLocate({ request: going ?? 0 }, going !== null);
  const stream = useRef<HTMLDivElement>(null);
  const pinned = useRef(true); // the person is at the bottom, so new entries scroll into view
  const entries = timeline(chat.messages, chat.items);
  const history = chat.messages
    .filter((m) => m.to === agent && m.from !== agent && m.landing !== 'withdrawn' && m.id > 0)
    .sort((a, b) => b.id - a.id)
    .map((m) => m.body);
  const last = entries.at(-1)?.id;
  const byId = new Map(chat.messages.map((m) => [m.id, m]));
  const target = picked != null ? (byId.get(picked) ?? null) : null;
  const pick = (m: ChatMessage) => setPicked((p) => (p === m.id ? null : m.id));
  const keyOf = (e: (typeof entries)[number]) => (e.kind === 'message' ? `m${e.id}` : `i${e.id}`);
  // Only an entry that arrives after the one that was last grows; older pages and a switch of scope appear at once.
  const seen = useRef<{ scope: ChatScope; last: string | null }>({ scope, last: null });
  const tail = seen.current.scope === scope && seen.current.last !== null ? entries.findIndex((e) => keyOf(e) === seen.current.last) : -1;
  useEffect(() => {
    const e = entries.at(-1);
    seen.current = { scope, last: e ? keyOf(e) : null };
  });
  const repin = () => {
    const el = stream.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  };

  useLayoutEffect(repin, [last, entries.length, chat.working.turn_running]);

  // New entries while scrolled up are counted, not scrolled to; the pill takes the person down.
  const count = useRef(entries.length);
  useEffect(() => {
    const grew = entries.length - count.current;
    count.current = entries.length;
    if (grew > 0 && !pinned.current && tail >= 0) setNewer((n) => n + grew);
  }, [entries.length, tail]);
  const bottom = () => {
    const el = stream.current;
    if (el) el.scrollTop = el.scrollHeight;
    pinned.current = true;
    setNewer(0);
  };

  // The agent's messages the person has now drawn in front of them leave the FOR YOU list (chat.seen); once per newest number.
  const said = chat.messages.filter((m) => m.from === agent && m.id > 0).reduce((n, m) => Math.max(n, m.id), 0);
  const told = useRef(0);
  useEffect(() => {
    if (chat.status === 'ready' && said > told.current) {
      told.current = said;
      act.seen(said);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [said, chat.status]);

  // Opening the chat reads what the agent sent to the Inbox about it (B9): results and messages, never a question that waits or a prompt.
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

  // A message number: scroll to it when it is drawn; else learn its chat from the daemon, switch to it and read back until it is there.
  const jump = (id: number) => {
    if (!jumpTo(id)) setGoing(id);
  };
  const where = locate.data?.scope;
  useEffect(() => {
    if (going === null || !where) return;
    if (where !== scope) return setScope(where);
    if (jumpTo(going)) return setGoing(null);
    if (chat.hasOlder && !chat.loadingOlder) chat.loadOlder();
    else if (!chat.hasOlder && !chat.loadingOlder) setGoing(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [going, where, scope, chat.hasOlder, chat.loadingOlder, entries.length]);

  if (chat.status === 'off') return <ChatOff agent={agent} detail={detail} />;
  return (
    <section className="chat">
      <div className="chat-head">
        <ChatStrip detail={detail} />
        <Segmented
          label="Which messages"
          value={scope}
          onChange={(v) => {
            setScope(v);
            setPicked(null);
          }}
          options={[
            { value: 'mine', label: 'Mine: just you and this agent', icon: 'chat' },
            { value: 'all', label: 'All: also what other agents say to each other', icon: 'team' },
          ]}
        />
      </div>
      <ChatBanner detail={detail} />
      <div
        className="stream col"
        ref={stream}
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          if (pinned.current) setNewer(0);
          if (el.scrollTop < 60 && chat.hasOlder && !chat.loadingOlder) chat.loadOlder();
        }}
      >
        <Skeleton shape="chat" loading={chat.status === 'loading'} label="the conversation" />
        {chat.status === 'error' && sentence(classify(chat.error)) ? (
          <div className="banner" role="alert">
            {sentence(classify(chat.error))}{' '}
            <button className="btn" onClick={chat.refetch}>
              Retry
            </button>
          </div>
        ) : null}
        {chat.hasOlder ? (
          <button
            className="icon older"
            disabled={chat.loadingOlder}
            aria-label="Older messages"
            title="Older messages"
            onClick={chat.loadOlder}
          >
            <Icon name="less" label="Older messages" />
          </button>
        ) : null}
        {chat.status === 'ready' && !entries.length ? <Empty icon="chat" name="No messages yet" hint={agent} /> : null}
        <AnimatedList>
          {entries.map((e, i) => (
            <AnimatedItem key={keyOf(e)} instant={i <= tail || tail < 0} onFrame={repin}>
              {e.kind === 'message' ? (
                <Message
                  m={e.message}
                  agent={agent}
                  picked={e.id === picked}
                  onPick={pick}
                  byId={byId}
                  working={chat.working.turn_running}
                  onRestore={(text) => setRestore({ text, n: (restore?.n ?? 0) + 1 })}
                  onRef={jump}
                />
              ) : (
                <Item i={e.item} />
              )}
            </AnimatedItem>
          ))}
        </AnimatedList>
        <HrCards agent={agent} />
        {chat.working.turn_running ? (
          <div className="line working" role="status" title={chat.working.line ?? 'Working'}>
            <Icon name="working" size={14} label={chat.working.line ?? 'Working'} aria-hidden />
            <span>{chat.working.line ?? 'Working'}…</span>
          </div>
        ) : null}
      </div>
      {newer ? (
        <button className="newer" onClick={bottom} title="New messages">
          <Icon name="newer" size={14} aria-hidden />
          {newer}
        </button>
      ) : null}
      <div className="cards">
        <PromptSlot agent={agent} prompt={detail.prompt} />
      </div>
      <Compose
        key={agent}
        agent={agent}
        detail={detail}
        storage={storage}
        canSend={canSend}
        history={history}
        messages={chat.messages}
        target={target}
        onClear={() => setPicked(null)}
        restore={restore}
        running={chat.working.turn_running}
      />
    </section>
  );
}
