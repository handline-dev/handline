// Chat tab: the conversation with the agent. The person's messages show how far they got (sending, delivered...), the agent's
// replies, tool lines and task events follow in time.
// A daemon that does not serve chat gets an empty state; the permission card shows either way.
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { AgentDetail, ChatMessage, TranscriptItem } from '@handline/client';
import { type TaskFold, type TimelineEntry } from '@handline/app-core';
import { useNearEnd, useSelector, useStore } from '@handline/app-react';
import {
  Skeleton,
  classify,
  sentence,
  useChat,
  useChatActions,
  useChatReadMarks,
  useChatLocate,
  useChatWindow,
  useServes,
  useTasks,
  type Chat,
  type ChatScope,
} from '@handline/app-data';
import { AnimatedItem, AnimatedList } from '#src/AnimatedList.tsx';
import { PromptSlot } from '#src/PromptCard.tsx';
import { Compose } from '#src/ChatCompose.tsx';
import { LiveLine } from '#src/LiveLine.tsx';
import { FoldLine, entryKey, useFolds } from '#src/ChatFold.tsx';
import { useCardTitles } from '#src/ChatTitles.ts';
import { Item, Message, jumpTo } from '#src/ChatMessages.tsx';
import { TaskNote } from '#src/ChatTaskCards.tsx';
import { ChatBanner, ChatStrip, DecidedCard, HandoffCard, HrCards, NeedsYouCard, RoomTitle, useDecided } from '#src/ChatCards.tsx';
import { ChatSearch } from '#src/ChatSearch.tsx';
import { Button, Empty, Icon, Textarea, ComposeRow } from '#src/ui/index.ts';
import { goSettings, useWantedMessage } from '#src/route.ts';
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

/** Scrolls `box` so the line `a` remembered is back where it was. Found again by its id: a page landing can re-create the element (separators, grouping). */
function putBack(box: HTMLElement, a: { id: string; off: number }) {
  const el = [...box.querySelectorAll('[data-id]')].find((n) => n.getAttribute('data-id') === a.id);
  if (!el) return;
  const drift = el.getBoundingClientRect().top - box.getBoundingClientRect().top - a.off;
  if (drift) box.scrollTop += drift;
}

/** The first line in view and how far below the top of `box` it sits: what a reader is looking at. */
function lineInView(box: HTMLElement): { id: string; off: number } | null {
  const { top } = box.getBoundingClientRect();
  for (const el of box.querySelectorAll('[data-id]')) {
    const at = el.getBoundingClientRect();
    const id = el.getAttribute('data-id');
    if (id && at.bottom > top) return { id, off: at.top - top };
  }
  return null;
}

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
  const me = useStore().me;
  // A reply item is an id of the transcript, so a daemon that serves no transcript has nothing to answer.
  const canReplyItem = useSelector((s) => !!s.client.hello?.capabilities.transcript);
  // Chat is the person and this agent only; the team's traffic is the Activity tab.
  const scope: ChatScope = 'mine';
  const { chat, win, windowed } = useWindowedChat(agent, scope);
  const [picked, setPicked] = useState<number | null>(null);
  const [newer, setNewer] = useState(0); // entries that arrived while the person was reading further up
  const [going, setGoing] = useState<number | null>(null); // a message number being found
  const act = useChatActions(agent);
  const locate = useChatLocate({ request: going ?? 0 }, going !== null);
  const stream = useRef<HTMLDivElement>(null);
  const pinned = useRef(true); // the person is at the bottom, so new entries scroll into view
  const { entries, ...folds } = useFolds(chat, scope, agent, me, useDecided(agent));
  // Task titles for the transcript's task lines, so a card can say what it is about without opening the task.
  const { served } = useServes('tasks.list');
  const tasks = useTasks({ all: true, limit: 200 }); // done tasks too: a card of a closed task keeps its title
  const titles = useCardTitles(served && tasks.data ? new Map(tasks.data.items.map((t) => [t.id, t.title])) : null, [
    ...chat.items.flatMap((i) => (i.task_event ? [i.task_event.task] : [])),
    ...entries.flatMap((e) => (e.kind === 'note' ? [e.task] : [])),
  ]);
  const history = chat.messages
    .filter((m) => m.to === agent && m.from !== agent && m.landing !== 'withdrawn' && m.id > 0)
    .sort((a, b) => b.id - a.id)
    .map((m) => m.body);
  const last = entries.at(-1)?.id;
  const byId = new Map(chat.messages.map((m) => [m.id, m]));
  const target = picked != null ? (byId.get(picked) ?? null) : null;
  // A message or one of the agent's own replies is picked, never both.
  const [pickedItem, setPickedItem] = useState<TranscriptItem | null>(null);
  const pick = (m: ChatMessage) => (setPickedItem(null), setPicked((p) => (p === m.id ? null : m.id)));
  const pickItem = (i: TranscriptItem) => (setPicked(null), setPickedItem((p) => (p?.id === i.id ? null : i)));
  const keyOf = entryKey;
  // Only an entry that arrives after the one that was last grows; older pages and a switch of scope appear at once.
  const seen = useRef<{ scope: ChatScope; last: string | null }>({ scope, last: null });
  const tail = seen.current.scope === scope && seen.current.last !== null ? entries.findIndex((e) => keyOf(e) === seen.current.last) : -1;
  useEffect(() => {
    const e = entries.at(-1);
    seen.current = { scope, last: e ? keyOf(e) : null };
  });
  // The latest page shows first; older pages load as the top comes near. The line in view when a load starts is put back in the layout effect of
  // each commit that changes the entries, before the sentinel is armed again.
  const anchor = useRef<{ id: string; off: number } | null>(null);
  const [armed, setArmed] = useState(true);
  const more = () => {
    const box = stream.current;
    if (!box || anchor.current) return;
    const { top, bottom } = box.getBoundingClientRect();
    // The last line in view: whatever page lands, it lands above it. The first one may be an old message above the paged window, which a page never moves.
    const el = [...box.querySelectorAll('[data-id]')]
      .filter((n) => n.getBoundingClientRect().top < bottom && n.getBoundingClientRect().bottom > top)
      .at(-1);
    const id = el?.getAttribute('data-id');
    anchor.current = el && id ? { id, off: el.getBoundingClientRect().top - top } : null;
    setArmed(false);
    chat.loadOlder();
  };
  useLayoutEffect(() => {
    const box = stream.current;
    const a = anchor.current;
    if (!box || !a) return;
    putBack(box, a);
    if (!chat.loadingOlder) anchor.current = null; // the load is over; the next one remembers its own line
  }, [entries.length, chat.loadingOlder]);
  useEffect(() => {
    if (chat.loadingOlder || anchor.current) return;
    const id = requestAnimationFrame(() => setArmed(true));
    return () => cancelAnimationFrame(id);
  }, [chat.loadingOlder, entries.length]);
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
  const own = useRef(false); // the next scroll event is our own jump to the bottom, not the person leaving it
  const repin = () => {
    const el = stream.current;
    if (!el || !pinned.current) return;
    const end = el.scrollHeight - el.clientHeight;
    if (Math.abs(el.scrollTop - end) >= 1) own.current = true;
    el.scrollTop = end;
  };

  useLayoutEffect(repin, [last, entries.length, chat.working.turn_running, chat.items.length]);

  // A reader above the bottom keeps the line they are on: whatever changes above or below it (steps folding, a line coming or going) does not move it.
  const place = useRef<{ id: string; off: number } | null>(null);
  useLayoutEffect(() => {
    const box = stream.current;
    if (box && place.current && !pinned.current && !anchor.current) putBack(box, place.current);
  });

  // New entries while scrolled up are counted, not scrolled to; the pill takes the person down.
  const count = useRef(entries.length);
  useEffect(() => {
    const grew = entries.length - count.current;
    count.current = entries.length;
    if (grew > 0 && !pinned.current && tail >= 0 && !windowed) setNewer((n) => n + grew);
  }, [entries.length, tail, windowed]);
  const bottom = () => {
    pinned.current = true;
    repin();
    setNewer(0);
  };
  // Sending always shows the message: the sent row is at the bottom, whatever the person had scrolled to.
  const showSent = () => {
    const e = entries.at(-1);
    if (e?.kind === 'message' && e.message.id < 0 && e.message.from === me) bottom();
  };
  useLayoutEffect(showSent, [last]); // eslint-disable-line react-hooks/exhaustive-deps

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
      pinned.current = false;
      win.open(going, found.messages, found.more_older, found.more_newer);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [going, where, scope, locate.data, windowed, entries.length]);

  const [finding, setFinding] = useFindBar();
  if (chat.status === 'off') return <ChatOff agent={agent} detail={detail} />;
  return (
    <section className={CHAT}>
      <div className="chat-head flex h-[var(--h-tabs)] items-center gap-2 border-b border-line px-2">
        <RoomTitle agent={agent} detail={detail} />
        {finding ? <ChatSearch agent={agent} scope={scope} onGo={jump} onClose={() => setFinding(false)} /> : null}
        <Button icon="settings" label={`${agent} settings`} onClick={() => goSettings(agent)} />
      </div>
      <ChatBanner detail={detail} />
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          className="stream col flex flex-1 flex-col gap-2 overflow-auto px-3 pb-2 pt-3 [overflow-anchor:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden [&>*]:flex-none [&>.skeleton]:self-stretch"
          ref={stream}
          onWheel={() => setMoved(win.target)}
          onTouchMove={() => setMoved(win.target)}
          onKeyDown={() => setMoved(win.target)}
          onLoadCapture={(e) => e.target instanceof HTMLImageElement && repin()} // a picture arriving late grows the stream
          onScroll={(e) => {
            const el = e.currentTarget;
            if (own.current) return void (own.current = false);
            pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
            chat.holdPlace(!pinned.current);
            place.current = pinned.current ? null : lineInView(el);
            if (pinned.current) setNewer(0);
          }}
        >
          <Skeleton shape="chat" loading={chat.status === 'loading'} label="the conversation" />
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
          {chat.status === 'ready' && !entries.length ? <Empty icon="chat" name="No messages yet" hint={agent} /> : null}
          {/* Keyed by the window: the rows of the live tail leave at once when a window opens, not after an exit animation above the target. */}
          <AnimatedList key={win.target ?? 'live'}>
            {entries.flatMap((e, i): ReactNode[] => {
              const day = dayHeading(e.at, i === 0 ? null : entries[i - 1]!.at);
              const body = (
                <AnimatedItem
                  key={keyOf(e)}
                  instant={i <= tail || tail < 0}
                  onFrame={repin}
                  className="max-w-full has-[>_.msg]:relative has-[>_.msg]:max-w-[80%] has-[>_.msg.person]:self-end has-[>_.msg:not(.person)]:self-start has-[>_.card-task]:self-start"
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
              return day
                ? [
                    <div
                      key={`day ${day}`}
                      role="separator"
                      className="mt-3 mb-0.5 flex items-center gap-2 text-label text-ink-2 before:flex-1 after:flex-1 before:border-t after:border-t before:border-line after:border-line before:content-[''] after:content-['']"
                    >
                      {day}
                    </div>,
                    body,
                  ]
                : [body];
            })}
          </AnimatedList>
          {windowed && win.hasNewer ? <div ref={lower} aria-hidden className="h-px" /> : null}
          <HrCards agent={agent} />
          <HandoffCard detail={detail} items={chat.items} />
          {detail.prompt ? null : <NeedsYouCard detail={detail} />}
          {/* The last row of the stream is always held, so the indicator coming and going never moves the messages above it. */}
          <LiveLine working={chat.working} messages={chat.messages} items={chat.items} />
        </div>
        {windowed ? <LatestPill onClick={() => (win.close(), bottom())} /> : newer ? <NewerPill count={newer} onClick={bottom} /> : null}
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
      <div className="chat-foot flex h-8 items-center border-t border-line px-3">
        <ChatStrip detail={detail} />
      </div>
    </section>
  );
}
