// Chat tab: the conversation with the agent. The person's messages show how far they got (sending, delivered, not answered...), the agent's
// replies, tool lines and task events follow in time, and a question from the agent can be answered or dismissed here.
// A daemon that does not serve chat gets an empty state; the permission card shows either way.
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
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
  useServes,
  useTasks,
  useWrite,
  type ChatScope,
} from '@handline/app-data';
import { AnimatedItem, AnimatedList } from './AnimatedList.tsx';
import { PromptSlot } from './PromptCard.tsx';
import { Compose } from './ChatCompose.tsx';
import { Item, Message, jumpTo } from './ChatMessages.tsx';
import { ChatBanner, ChatStrip, HrCards } from './ChatCards.tsx';
import { Button, Empty, Icon, Segmented } from './ui/index.ts';
import { setting } from './daemon.ts';
import { useWorkspace } from './Workspace.tsx';
import { COMPOSE, COMPOSE_ROW } from './chatStyle.ts';

/** The heading where the day turns — 'Oct 11'; null on the same day. */
function dayHeading(at: string, before: string | null): string | null {
  const d = new Date(at);
  if (Number.isNaN(+d)) return null;
  const p = before ? new Date(before) : null;
  if (p && !Number.isNaN(+p) && d.toDateString() === p.toDateString()) return null;
  const year = d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric';
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year });
}

function ChatOff({ agent, detail }: { agent: string; detail: AgentDetail }) {
  return (
    <section className="chat relative flex min-h-0 min-w-0 flex-col border-r border-line [&>*]:mx-auto [&>*]:w-full [&>*]:max-w-[56rem]">
      <div className="stream flex flex-1 overflow-auto">
        <Empty
          icon="chat"
          name="No chat yet"
          hint={detail.structured ? 'Send a message to start' : 'Use the terminal tab to work with the agent'}
        />
      </div>
      <div className="cards px-3">
        <PromptSlot agent={agent} prompt={detail.prompt} />
      </div>
      <div className={COMPOSE_ROW}>
        <textarea
          className={COMPOSE}
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
  // The scope comes back next visit, as the bridge kept it.
  const keptScope = setting(storage, 'handline.chatScope', 'mine');
  const [scope, setScope] = useState<ChatScope>(keptScope.value === 'all' ? 'all' : 'mine');
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
  // Task titles for the transcript's task lines, so a card can say what it is about without opening the task.
  const { served } = useServes('tasks.list');
  const tasks = useTasks({});
  const titles = new Map((served ? (tasks.data?.items ?? []) : []).map((t) => [t.id, t.title]));
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
    <section className="chat relative flex min-h-0 min-w-0 flex-col border-r border-line [&>*]:mx-auto [&>*]:w-full [&>*]:max-w-[56rem]">
      <div className="chat-head flex h-[var(--h-tabs)] items-center justify-between gap-2 border-b border-line px-2">
        <ChatStrip detail={detail} />
        <Segmented
          label="Which messages"
          value={scope}
          onChange={(v) => {
            setScope(v);
            keptScope.save(v);
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
        className="stream col flex flex-1 flex-col gap-2 overflow-auto p-3 [&>*]:flex-none"
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
            className="icon older self-center"
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
          {entries.flatMap((e, i): ReactNode[] => {
            const day = dayHeading(e.at, i === 0 ? null : entries[i - 1]!.at);
            const body = (
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
                  <Item i={e.item} titles={titles} />
                )}
              </AnimatedItem>
            );
            // A plain child of the list: never animated in or out.
            return day ? (
              [
                <div
                  key={`day ${day}`}
                  role="separator"
                  className="mt-2 mb-0.5 flex items-center gap-2 text-label text-ink-2 before:flex-1 after:flex-1 before:border-t after:border-t before:border-line after:border-line before:content-[''] after:content-['']"
                >
                  {day}
                </div>,
                body,
              ]
            ) : (
              [body]
            );
          })}
        </AnimatedList>
        <HrCards agent={agent} />
        {chat.working.turn_running ? (
          <div className="line working flex items-center gap-1.5 self-start text-[12px] text-working" role="status" title={chat.working.line ?? 'Working'}>
            <Icon name="working" size={14} label={chat.working.line ?? 'Working'} aria-hidden />
            <span>{chat.working.line ?? 'Working'}…</span>
          </div>
        ) : null}
      </div>
      {newer ? (
        <button className="newer absolute right-4 bottom-[96px] inline-flex items-center gap-1 rounded-pill border border-line-strong bg-panel px-2 py-1 shadow-[var(--shadow-pop)]" onClick={bottom} title="New messages">
          <Icon name="newer" size={14} aria-hidden />
          {newer}
        </button>
      ) : null}
      <div className="cards px-3">
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
