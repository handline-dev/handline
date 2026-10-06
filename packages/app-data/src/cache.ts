// Small helpers for editing cached query data. Both the event bridge and the optimistic writes use them.
import type { InfiniteData, QueryClient } from '@tanstack/react-query';
import type { Key } from './reads.ts';

export type Pages<P> = InfiniteData<P, number | undefined>;

/** What an edit needs to know: the cache, which daemon's entries, who the person is, and the time. */
export interface Ctx {
  qc: QueryClient;
  d: string;
  me: string;
  now: string;
}

/** Edits every cached entry under `key` that has data; an entry nobody has read yet is left for its first read. */
export function edit<T>(qc: QueryClient, key: Key, fn: (data: T) => T): void {
  qc.setQueriesData<T>({ queryKey: key }, (data) => (data === undefined ? data : fn(data)));
}

/** `list` with `item` in place of the entry `same` finds, or added at the end (or the start). */
export function upsert<T>(list: T[], item: T, same: (x: T) => boolean, at: 'end' | 'start' = 'end'): T[] {
  return list.some(same) ? list.map((x) => (same(x) ? item : x)) : at === 'end' ? [...list, item] : [item, ...list];
}

export const mapPages = <P>(data: Pages<P>, f: (page: P, index: number) => P): Pages<P> => ({ ...data, pages: data.pages.map(f) });

/** A negative id for a row the daemon has not numbered yet; each call gives a new one. */
let provisional = 0;
export const provisionalId = () => -(Date.now() * 1000 + (provisional++ % 1000));
