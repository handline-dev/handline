// Chat tab: the conversation with the agent. The person's messages show how far they got (sending, delivered, not answered...), the agent's
// replies, tool lines and task events follow in time, and a question from the agent can be answered or dismissed here.
// A daemon that does not serve chat gets an empty state; the permission card shows either way.
import { useLayoutEffect, useRef, useState } from 'react';
import type { AgentDetail } from '@handline/client';
import { timeline } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { Skeleton, classify, sentence, useChat, type ChatScope } from '@handline/app-data';
import { PromptSlot } from './PromptCard.tsx';
import { Compose } from './ChatCompose.tsx';
import { Item, Message } from './ChatMessages.tsx';
import { useWorkspace } from './Workspace.tsx';

function ChatOff({ agent, detail }: { agent: string; detail: AgentDetail }) {
  return (
    <section className="chat">
      <div className="stream">
        <div className="empty">
          <strong>No chat yet</strong>
          <p>This daemon does not serve the conversation. Use the terminal tab to work with the agent.</p>
        </div>
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
        <button className="btn" disabled>
          Send ↵
        </button>
      </div>
    </section>
  );
}

export function ChatTab({ agent, detail }: { agent: string; detail: AgentDetail }) {
  const { storage } = useWorkspace();
  const canSend = useSelector((s) => s.can('chat.send'));
  const [scope, setScope] = useState<ChatScope>('mine');
  const chat = useChat(agent, scope);
  const stream = useRef<HTMLDivElement>(null);
  const pinned = useRef(true); // the person is at the bottom, so new entries scroll into view
  const entries = timeline(chat.messages, chat.items);
  const history = chat.messages
    .filter((m) => m.to === agent && m.from !== agent && m.landing !== 'withdrawn' && m.id > 0)
    .sort((a, b) => b.id - a.id)
    .map((m) => m.body);
  const last = entries.at(-1)?.id;

  useLayoutEffect(() => {
    const el = stream.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [last, entries.length, chat.working.turn_running]);

  if (chat.status === 'off') return <ChatOff agent={agent} detail={detail} />;
  return (
    <section className="chat">
      <div className="chat-tabs" role="tablist" aria-label="Which messages">
        {(['mine', 'all'] as const).map((s) => (
          <button
            key={s}
            role="tab"
            aria-selected={scope === s}
            className={`chat-tab${scope === s ? ' on' : ''}`}
            onClick={() => setScope(s)}
          >
            {s === 'mine' ? 'Mine' : 'All'}
          </button>
        ))}
      </div>
      <div
        className="stream col"
        ref={stream}
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
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
          <button className="btn older" disabled={chat.loadingOlder} onClick={chat.loadOlder}>
            Older messages
          </button>
        ) : null}
        {chat.status === 'ready' && !entries.length ? (
          <div className="empty">
            <strong>No messages yet</strong>
            <p>Write to {agent} below.</p>
          </div>
        ) : null}
        {entries.map((e) =>
          e.kind === 'message' ? <Message key={`m${e.id}`} m={e.message} agent={agent} /> : <Item key={`i${e.id}`} i={e.item} />,
        )}
        {chat.working.turn_running ? (
          <div className="line working" role="status">
            ● {chat.working.line ?? 'Working'}…
          </div>
        ) : null}
      </div>
      <div className="cards">
        <PromptSlot agent={agent} prompt={detail.prompt} />
      </div>
      <Compose key={agent} agent={agent} storage={storage} canSend={canSend} history={history} />
    </section>
  );
}
