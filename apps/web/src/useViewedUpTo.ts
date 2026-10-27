// The newest of the agent's messages that has been in view in the chat's stream: what chat.seen is sent for, not the newest drawn.
import { useEffect, useState, type RefObject } from 'react';
import type { Chat } from '@handline/app-data';

export function useViewedUpTo(stream: RefObject<HTMLElement | null>, agent: string, chat: Pick<Chat, 'status' | 'messages'>): number {
  const [upTo, setUpTo] = useState(0);
  const { messages, status } = chat;
  useEffect(() => {
    const root = stream.current;
    const measure = () => {
      if (!root || status !== 'ready') return;
      const said = new Set(messages.filter((m) => m.from === agent && m.id > 0).map((m) => m.id));
      const box = root.getBoundingClientRect();
      let max = 0;
      for (const el of root.querySelectorAll<HTMLElement>('.msg[data-id]')) {
        const id = Number(el.dataset.id);
        const r = el.getBoundingClientRect();
        if (said.has(id) && r.bottom > box.top && r.top < box.bottom) max = Math.max(max, id);
      }
      setUpTo((u) => Math.max(u, max));
    };
    // After the rows are drawn, so their places are measured.
    const frame = requestAnimationFrame(measure);
    root?.addEventListener('scroll', measure, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      root?.removeEventListener('scroll', measure);
    };
  }, [stream, agent, messages, status]);
  return upTo;
}
