// Chat tab: the conversation with the agent. The person's messages show how far they got (sending, delivered...), the agent's
// replies, tool lines and task events follow in time.
// A daemon that does not serve chat gets an empty state; the permission card shows either way.
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { AgentDetail, ChatMessage, TranscriptItem } from '@handline/client';
import { restartShown, sideOf, type TaskFold, type TimelineEntry } from '@handline/app-core';
import { useMe, useNearEnd, useSelector } from '@handline/app-react';
import {
  Skeleton,
  useSkeletonGate,
  classify,
  sentence,
  useChat,
  useChatActions,
  useChatReadMarks,
  useChatLocate,
  useChatWindow,
  type Chat,
  type ChatScope,
} from '@handline/app-data';
import { AnimatedItem, AnimatedList } from '#src/AnimatedList.tsx';
import { PromptSlot } from '#src/PromptCard.tsx';
import { Compose } from '#src/ChatCompose.tsx';
import { LiveLine } from '#src/LiveLine.tsx';
import { useChatScroll } from '#src/useChatScroll.ts';
import { sentBodies, useOlderPages, useTail } from '#src/chatLines.ts';
import { FoldLine, entryKey, useFolds } from '#src/ChatFold.tsx';
import { useCardTitles } from '#src/ChatTitles.ts';
import { Item, Message, jumpTo } from '#src/ChatMessages.tsx';
import { TaskNote } from '#src/ChatTaskCards.tsx';
import { ChatBanner, ChatStrip, DecidedCard, HandoffCard, HrCards, NeedsYouCard, useDecided } from '#src/ChatCards.tsx';
import { ChatSearch } from '#src/ChatSearch.tsx';
import { Button, Empty, Icon, Textarea, ComposeRow } from '#src/ui/index.ts';
import { useWantedMessage } from '#src/route.ts';
import { useWorkspace } from '#src/Workspace.tsx';

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
    <section className={CHAT}>
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
      <ComposeRow>
        <Textarea
          className="compose flex-1 resize-none border-0 bg-transparent px-1.5"
          rows={2}
          placeholder="Chat is not available from this daemon yet"
          disabled
          aria-label="Message to the agent"
        />
        <Button variant="solid" icon="send" label="Send" disabled />
      </ComposeRow>
    </section>
  );
}

const CHAT = 'chat relative flex min-h-0 min-w-0 flex-col border-r border-line [&>*]:mx-auto [&>*]:w-full [&>*]:max-w-[56rem]';

/** The count of messages that came while the person reads up; a click takes them down. */
function NewerPill({ count, onClick }: { count: number; onClick: () => void }) {
  return (
    // CHAT stretches every direct child to full width, so the row spans it and only the pill takes clicks.
    <div className="pointer-events-none absolute inset-x-0 bottom-3 z-10 flex justify-center">
      <button
        className="newer pointer-events-auto inline-flex items-center gap-1 rounded-pill bg-panel px-3 py-1 text-label shadow-[var(--shadow-2)]"
        onClick={onClick}
      >
        {count} new {count === 1 ? 'message' : 'messages'}
        <Icon name="newer" size={14} aria-hidden />
      </button>
    </div>
  );
}

/** Shown while a window around an old message is open: it takes the person back to the live tail. */
function LatestPill({ onClick }: { onClick: () => void }) {
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-3 z-10 flex justify-center">
      <button
        className="latest pointer-events-auto inline-flex items-center gap-1 rounded-pill bg-panel px-3 py-1 text-label shadow-[var(--shadow-2)]"
        onClick={onClick}
      >
        Back to latest
        <Icon name="newer" size={14} aria-hidden />
      </button>
    </div>
  );
}

/** The live chat, or while a window around an old message is open the stretch the daemon returned; the live transcript lines are kept only within its time span. */
function useWindowedChat(agent: string, scope: ChatScope) {
  const live = useChat(agent, scope);
  const win = useChatWindow(agent, scope);
  const windowed = win.target !== null;
  const first = win.messages[0]?.at ?? '';
  const last = win.messages.at(-1)?.at ?? '';
  const chat: Chat = windowed
    ? {
        ...live,
        messages: win.messages,
        items: live.items.filter((i) => i.at >= first && i.at <= last),
        moreMessages: false,
        moreItems: false,
        hasOlder: win.hasOlder,
        loadingOlder: win.loading,
        loadOlder: win.loadOlder,
      }
    : live;
  return { chat, win, windowed };
}

/** In the all room, the sender's name over a message, left out when the entry before is a message from the same sender. */
const groupOf = (scope: ChatScope, prev: TimelineEntry | TaskFold | undefined, m: ChatMessage) =>
  scope === 'all' ? { name: prev?.kind === 'message' && prev.message.from === m.from ? null : m.from } : undefined;

/** ⌘F opens the find bar of this chat. */
function useFindBar() {
  const [finding, setFinding] = useState(false);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.key.toLowerCase() !== 'f') return;
      e.preventDefault();
      setFinding(true);
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  }, []);
  return [finding, setFinding] as const;
}

export function ChatTab({ agent, detail }: { agent: string; detail: AgentDetail }) {
  const { storage } = useWorkspace();
  const canSend = useSelector((s) => s.can('chat.send'));
  const me = useMe();
  // A reply item is an id of the transcript, so a daemon that serves no transcript has nothing to answer.
  const canReplyItem = useSelector((s) => !!s.client.hello?.capabilities.transcript);
  // Chat is the person and this agent only; the team's traffic is the Activity tab.
  const scope: ChatScope = 'mine';
  const { chat, win, windowed } = useWindowedChat(agent, scope);
  const [picked, setPicked] = useState<number | null>(null);
  const [going, setGoing] = useState<number | null>(null); // a message number being found
  const act = useChatActions(agent);
  const locate = useChatLocate({ request: going ?? 0 }, going !== null);
  const stream = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const { entries, ...folds } = useFolds(chat, scope, agent, me, useDecided(agent));
  const titles = useCardTitles(chat.items, entries); // for the task cards: what each is about, without opening the task
  const history = sentBodies(chat.messages, agent);
  // The restart line sits where the restart began: what was sent while it ran reads below it.
  const since = detail.handoff && restartShown(detail.handoff, chat.items) ? detail.handoff.since : null;
  const restartAt = since === null ? -1 : entries.findIndex((e) => e.at >= since);
  const last = entries.at(-1)?.id;
  const byId = new Map(chat.messages.map((m) => [m.id, m]));
  const target = picked != null ? (byId.get(picked) ?? null) : null;
  // A message or one of the agent's own replies is picked, never both.
  const [pickedItem, setPickedItem] = useState<TranscriptItem | null>(null);
  const pick = (m: ChatMessage) => (setPickedItem(null), setPicked((p) => (p === m.id ? null : m.id)));
  const pickItem = (i: TranscriptItem) => (setPicked(null), setPickedItem((p) => (p?.id === i.id ? null : i)));
  const keyOf = entryKey;
  const tail = useTail(entries, scope);
  const { armed, more } = useOlderPages(stream, chat, entries.length);
  // After a jump the target stays where it was put: the sentinels wait until the person scrolls the window themselves.
  const [moved, setMoved] = useState<number | null>(null);
  const reading = !windowed || moved === win.target;
  const top = useNearEnd<HTMLDivElement, HTMLDivElement>(
    stream,
    more,
    armed && reading && chat.hasOlder && !chat.loadingOlder && chat.status === 'ready',
    { atTop: true },
  );
  // Around an old message, the way down is read as the bottom comes near, until the live tail is reached.
  const lower = useNearEnd<HTMLDivElement, HTMLDivElement>(stream, win.loadNewer, windowed && reading && win.hasNewer && !win.loading);
  // The skeleton and the lines never share a frame: the lines are drawn in the commit that removes it, with no minimum time for it to stay.
  const loading = chat.status === 'loading';
  const skeleton = useSkeletonGate(loading, undefined, 0);
  const drawn = !loading && !skeleton;
  const scroll = useChatScroll({ stream, content, ready: chat.status === 'ready' && drawn, agent, reading: chat.holdPlace });

  // New entries while the reader is above the bottom are counted, not scrolled to; the pill takes them down.
  const count = useRef(entries.length);
  useEffect(() => {
    const grew = entries.length - count.current;
    count.current = entries.length;
    if (grew > 0 && tail >= 0 && !windowed) scroll.added(grew);
  }, [entries.length, tail, windowed]); // eslint-disable-line react-hooks/exhaustive-deps
  const bottom = () => scroll.bottom();
  // Sending always shows the message: the sent row is at the bottom, whatever the person had scrolled to.
  useLayoutEffect(() => {
    const e = entries.at(-1);
    if (e?.kind === 'message' && e.message.id < 0 && sideOf(e.message, agent, me) === 'person') scroll.sent();
  }, [last]); // eslint-disable-line react-hooks/exhaustive-deps

  useChatReadMarks(agent, chat, act.seen);

  // A message number: scroll to it when it is drawn; else learn its chat from the daemon, switch to it and read back until it is there.
  const jump = (id: number) => {
    if (!jumpTo(id)) setGoing(id);
  };
  // A message chosen in the palette: this chat is its agent's, so find it the same way.
  const wanted = useWantedMessage(agent);
  useEffect(() => {
    if (wanted.id === null || chat.status !== 'ready') return;
    jump(wanted.id);
    wanted.done();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted.id, chat.status]);
  const where = locate.data?.scope;
  useEffect(() => {
    if (going === null || !where) return;
    // Said between agents: there is no tab that shows it.
    if (where !== scope) return setGoing(null);
    if (jumpTo(going)) return setGoing(null);
    const found = locate.data;
    if (!found) return;
    if (win.target !== going) {
      // Not drawn: the stretch around it is read at once, not paged up to.
      scroll.leave();
      win.open(going, found.messages, found.more_older, found.more_newer);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [going, where, scope, locate.data, windowed, entries.length]);

  const [finding, setFinding] = useFindBar();
  if (chat.status === 'off') return <ChatOff agent={agent} detail={detail} />;
  return (
    <section className={CHAT}>
      {finding ? (
        <div className="chat-find flex items-center gap-2 border-b border-line px-2 py-1">
          <ChatSearch agent={agent} scope={scope} onGo={jump} onClose={() => setFinding(false)} />
        </div>
      ) : null}
      <ChatBanner detail={detail} />
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          className="stream col flex-1 overflow-auto [overflow-anchor:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          ref={stream}
          onWheel={() => (scroll.driven(), setMoved(win.target))}
          onTouchMove={() => (scroll.driven(), setMoved(win.target))}
          onKeyDown={() => (scroll.driven(), setMoved(win.target))}
          onPointerDown={scroll.driven}
          onScroll={scroll.onScroll}
        >
          <div ref={content} className="flex min-h-full flex-col gap-2 px-3 pb-2 pt-3 [&>*]:flex-none [&>.skeleton]:self-stretch">
            <Skeleton shape="chat" loading={loading} shown={skeleton} label="the conversation" />
            {chat.status === 'error' && sentence(classify(chat.error)) ? (
              <div className="banner" role="alert">
                {sentence(classify(chat.error))} <Button variant="outline" size="sm" label="Retry" onClick={chat.refetch} />
              </div>
            ) : null}
            {chat.hasOlder ? <div ref={top} aria-hidden className="h-px" /> : null}
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
            {chat.status === 'ready' && drawn && !entries.length ? <Empty icon="chat" name="No messages yet" hint={agent} /> : null}
            {/* Keyed by the window: the rows of the live tail leave at once when a window opens, not after an exit animation above the target. */}
            <AnimatedList key={win.target ?? 'live'}>
              {(drawn ? entries : []).flatMap((e, i): ReactNode[] => {
                const day = dayHeading(e.at, i === 0 ? null : entries[i - 1]!.at);
                const body = (
                  <AnimatedItem
                    key={keyOf(e)}
                    instant={i <= tail || tail < 0}
                    className="max-w-full has-[>_.msg]:relative has-[>_.msg]:max-w-[80%] has-[>_.msg.person]:self-end has-[>_.msg:not(.person)]:self-start has-[>_.card-task]:self-stretch"
                  >
                    {e.kind === 'fold' ? (
                      <FoldLine f={e} folds={folds} titles={titles} agent={agent} />
                    ) : e.kind === 'message' ? (
                      <Message
                        m={e.message}
                        agent={agent}
                        picked={e.id === picked}
                        onPick={pick}
                        byId={byId}
                        group={groupOf(scope, entries[i - 1], e.message)}
                      />
                    ) : e.kind === 'proposal' ? (
                      <DecidedCard p={e.proposal} />
                    ) : e.kind === 'note' ? (
                      <TaskNote m={e.message} task={e.task} titles={titles} />
                    ) : (
                      <Item
                        i={e.item}
                        titles={titles}
                        agent={agent}
                        onReply={canSend && canReplyItem ? pickItem : undefined}
                        picked={pickedItem?.id === e.item.id}
                      />
                    )}
                  </AnimatedItem>
                );
                // A plain child of the list: never animated in or out.
                const restart = i === restartAt;
                return day
                  ? [
                      restart && <HandoffCard key="handoff" detail={detail} items={chat.items} />,
                      <div
                        key={`day ${day}`}
                        role="separator"
                        className="mt-3 mb-0.5 flex items-center gap-2 text-label text-ink-2 before:flex-1 after:flex-1 before:border-t after:border-t before:border-line after:border-line before:content-[''] after:content-['']"
                      >
                        {day}
                      </div>,
                      body,
                    ]
                  : [restart && <HandoffCard key="handoff" detail={detail} items={chat.items} />, body];
              })}
            </AnimatedList>
            {windowed && win.hasNewer ? <div ref={lower} aria-hidden className="h-px" /> : null}
            {drawn ? <HrCards agent={agent} /> : null}
            {drawn && restartAt < 0 ? <HandoffCard detail={detail} items={chat.items} /> : null}
            {drawn && !detail.prompt ? <NeedsYouCard detail={detail} /> : null}
            {/* The last row of the stream is always held, so the indicator coming and going never moves the messages above it. */}
            <LiveLine working={chat.working} messages={chat.messages} items={chat.items} />
          </div>
        </div>
        {windowed ? (
          <LatestPill onClick={() => (win.close(), bottom())} />
        ) : scroll.newer ? (
          <NewerPill count={scroll.newer} onClick={bottom} />
        ) : null}
      </div>
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
        item={pickedItem}
        onClear={() => (setPicked(null), setPickedItem(null))}
        running={chat.working.turn_running}
      />
      <div className="chat-foot flex h-8 max-w-none! items-center border-t border-line px-3">
        <ChatStrip detail={detail} />
      </div>
    </section>
  );
}
