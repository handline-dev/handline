// Tells which of the agent's messages has been in view in the chat's stream, newest first seen: what chat.seen is sent for, not the newest drawn.
// It keeps no state, so a message arriving draws nothing more than itself.
import { useEffect, useRef, type RefObject } from 'react';
import type { Chat } from '@handline/app-data';

export function useViewedUpTo(
  stream: RefObject<HTMLElement | null>,
  agent: string,
  chat: Pick<Chat, 'status' | 'messages'>,
  onView: (up_to: number) => void,
): void {
  const { messages, status } = chat;
  const tell = useRef(onView);
  useEffect(() => {
    tell.current = onView;
  });
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
      if (max) tell.current(max);
    };
    // After the rows are drawn, so their places are measured.
    const frame = requestAnimationFrame(measure);
    root?.addEventListener('scroll', measure, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      root?.removeEventListener('scroll', measure);
    };
  }, [stream, agent, messages, status]);
}
