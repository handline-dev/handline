// The agents and tasks the person opened last, newest first, kept in the browser for the palette's RECENT and for Ctrl+Tab.
import { useEffect } from 'react';

export type Recent = { kind: 'agent' | 'task'; id: string };
const KEY = 'handline.recent';
const KEEP = 8;

export function recents(): Recent[] {
  try {
    const list: unknown = JSON.parse(localStorage.getItem(KEY) ?? '[]');
    return Array.isArray(list)
      ? list.filter((r): r is Recent => !!r && (r.kind === 'agent' || r.kind === 'task') && typeof r.id === 'string')
      : [];
  } catch {
    return [];
  }
}

/** Puts `r` first; one already in the list moves up. */
export function remember(r: Recent): void {
  try {
    const rest = recents().filter((x) => x.kind !== r.kind || x.id !== r.id);
    localStorage.setItem(KEY, JSON.stringify([r, ...rest].slice(0, KEEP)));
  } catch {
    /* private window: nothing is remembered */
  }
}

/** Records the agent or task the screen shows now. */
export function useRemember(agent: string, task: string | null): void {
  useEffect(() => {
    if (agent) remember({ kind: 'agent', id: agent });
  }, [agent]);
  useEffect(() => {
    if (task) remember({ kind: 'task', id: task });
  }, [task]);
}
