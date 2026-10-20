// What opening an agent's chat marks as read, for the PC chat and the phone's: the agent's newest message (chat.seen) and what it sent
// to the Inbox about itself.
import { useEffect, useRef } from 'react';
import type { Chat } from './chat.ts';
import { useInbox } from './read.ts';
import { useWrite } from './write.ts';

export function useChatReadMarks(agent: string, chat: Pick<Chat, 'status' | 'messages'>, seen: (upTo: number) => void): void {
  // The agent's messages now in front of the person leave the FOR YOU list (chat.seen); once per newest number.
  const said = chat.messages.filter((m) => m.from === agent && m.id > 0).reduce((n, m) => Math.max(n, m.id), 0);
  const told = useRef(0);
  useEffect(() => {
    if (chat.status === 'ready' && said > told.current) {
      told.current = said;
      seen(said);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [said, chat.status]);

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
}
