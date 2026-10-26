// Small readings of a chat's lines that ChatTab uses.
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import type { ChatMessage } from '@handline/client';
import type { TaskFold, TimelineEntry } from '@handline/app-core';
import type { ChatScope } from '@handline/app-data';
import { entryKey } from '#src/ChatFold.tsx';

/** Where the entries that arrived since the last render begin: only those grow; older pages and a switch of scope appear at once (-1 when none is new). */
export function useTail(entries: (TimelineEntry | TaskFold)[], scope: ChatScope) {
  const seen = useRef<{ scope: ChatScope; last: string | null }>({ scope, last: null });
  const tail =
    seen.current.scope === scope && seen.current.last !== null ? entries.findIndex((e) => entryKey(e) === seen.current.last) : -1;
  useEffect(() => {
    const e = entries.at(-1);
    seen.current = { scope, last: e ? entryKey(e) : null };
  });
  return tail;
}

/** What the person sent this agent, newest first: the composer's arrow-up history. */
export const sentBodies = (messages: ChatMessage[], agent: string) =>
  messages
    .filter((m) => m.to === agent && m.from !== agent && m.landing !== 'withdrawn' && m.id > 0)
    .sort((a, b) => b.id - a.id)
    .map((m) => m.body);

/** Scrolls `box` so the line `a` remembered is back where it was. Found again by its id: a page landing can re-create the element (separators, grouping). */
export function putBack(box: HTMLElement, a: { id: string; off: number }) {
  const el = [...box.querySelectorAll('[data-id]')].find((n) => n.getAttribute('data-id') === a.id);
  if (!el) return;
  const drift = el.getBoundingClientRect().top - box.getBoundingClientRect().top - a.off;
  if (drift) box.scrollTop += drift;
}

/**
 * The latest page shows first; older pages load as the top comes near. The line in view when a load starts is put back in the layout effect of
 * each commit that changes the entries, before the sentinel is armed again.
 */
export function useOlderPages(
  stream: RefObject<HTMLDivElement | null>,
  chat: { loadOlder: () => void; loadingOlder: boolean },
  entryCount: number,
) {
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
  }, [stream, entryCount, chat.loadingOlder]);
  useEffect(() => {
    if (chat.loadingOlder || anchor.current) return;
    const id = requestAnimationFrame(() => setArmed(true));
    return () => cancelAnimationFrame(id);
  }, [chat.loadingOlder, entryCount]);
  return { anchor, armed, more };
}
