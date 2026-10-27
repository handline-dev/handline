// What opening an agent's chat marks as read, for the PC chat and the phone's: the agent's newest message (chat.seen) and what it sent
// to the Inbox about itself.
import { useEffect, useRef } from 'react';
import type { Chat } from '#src/chat.ts';
import { useInbox } from '#src/read.ts';
import { useWrite } from '#src/write.ts';

/**
 * Returns what to call with the newest message number that has been in view. With `byView` the agent's messages are marked only
 * that way; without it the newest drawn counts (the phone's chat).
 */
export function useChatReadMarks(agent: string, chat: Pick<Chat, 'status' | 'messages'>, byView = false): (up_to: number) => void {
  const mark = useWrite('chat.seen', agent);
  // The agent's messages in front of the person leave the FOR YOU list (chat.seen); once per newest number.
  const told = useRef(0);
  const ready = useRef(false);
  useEffect(() => {
    ready.current = chat.status === 'ready';
  }, [chat.status]);
  const seen = (up_to: number) => {
    if (!ready.current || up_to <= told.current) return;
    told.current = up_to;
    mark.mutate({ agent, up_to });
  };
  const drawn = byView ? 0 : chat.messages.filter((m) => m.from === agent && m.id > 0).reduce((n, m) => Math.max(n, m.id), 0);
  useEffect(() => {
    seen(drawn);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawn, chat.status]);

  // Results and messages the agent sent to the Inbox are read once its chat is open, never a question that waits or a prompt.
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
  return seen;
}
