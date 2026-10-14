// Finds a message number in the chat: its chat comes from chat.locate, and older pages are read until it is drawn.
import { useEffect } from 'react';
import { useChatLocate, type Chat, type ChatScope } from '@handline/app-data';

export function ChatFind(props: {
  id: number;
  scope: ChatScope;
  setScope: (s: ChatScope) => void;
  chat: Pick<Chat, 'status' | 'hasOlder' | 'loadingOlder' | 'loadOlder'>;
  drawn: boolean;
  onFound: () => void;
  onLost: () => void;
}) {
  const { id, scope, chat, drawn } = props;
  const locate = useChatLocate({ request: id });
  const where = locate.data?.scope;
  const { onFound, onLost, setScope } = props;
  useEffect(() => {
    if (drawn) return onFound();
    if (locate.isError) return onLost();
    if (!where || chat.status !== 'ready') return;
    // All shows what Mine shows, so only Mine can need a switch.
    if (scope === 'mine' && where === 'all') return setScope('all');
    if (chat.loadingOlder) return;
    if (chat.hasOlder) chat.loadOlder();
    else onLost();
  }, [drawn, locate.isError, where, scope, chat.status, chat.hasOlder, chat.loadingOlder, chat, onFound, onLost, setScope]);
  return null;
}
