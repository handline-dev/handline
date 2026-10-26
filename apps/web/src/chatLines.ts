// Small readings of a chat's lines that ChatTab uses.
import { useEffect, useRef } from 'react';
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
