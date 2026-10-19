// Finds a message number in the chat: its chat comes from chat.locate, and older pages are read until it is drawn.
import { useEffect } from 'react';
import { useChatLocate, type Chat } from '@handline/app-data';

export function ChatFind(props: {
  id: number;
  chat: Pick<Chat, 'status' | 'hasOlder' | 'loadingOlder' | 'loadOlder'>;
  drawn: boolean;
  onFound: () => void;
  onLost: () => void;
}) {
  const { id, chat, drawn } = props;
  const locate = useChatLocate({ request: id });
  const where = locate.data?.scope;
  const { onFound, onLost } = props;
  useEffect(() => {
    if (drawn) return onFound();
    if (locate.isError) return onLost();
    if (!where || chat.status !== 'ready') return;
    if (where === 'all') return onLost(); // the message is in Activity, not in this chat
    if (chat.loadingOlder) return;
    if (chat.hasOlder) chat.loadOlder();
    else onLost();
  }, [drawn, locate.isError, where, chat.status, chat.hasOlder, chat.loadingOlder, chat, onFound, onLost]);
  return null;
}
