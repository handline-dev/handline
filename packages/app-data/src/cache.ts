// Small helpers for editing cached query data. Both the event bridge and the optimistic writes use them.
import type { InfiniteData, QueryClient } from '@tanstack/react-query';
import type { Key } from '#src/reads.ts';

export type Pages<P> = InfiniteData<P, number | undefined>;

/** What an edit needs to know: the cache, which daemon's entries, who the person is, and the time. */
export interface Ctx {
  qc: QueryClient;
  d: string;
  me: string;
  now: string;
}

/** The stream position a read answered at: the `cursor` of the result, or of the newest page of a paged one. */
function readAt(data: unknown): number | undefined {
  const one = (x: unknown) => (typeof x === 'object' && x !== null ? (x as { cursor?: unknown }).cursor : undefined);
  const pages = (data as { pages?: unknown[] } | null)?.pages;
  const at = Array.isArray(pages) ? one(pages[0]) : one(data);
  return typeof at === 'number' ? at : undefined;
}

/** Whether an event at `cursor` is already in what a read answered: the read says so with its own cursor. */
export const inRead = (data: unknown, cursor: number | undefined): boolean => {
  const at = readAt(data);
  return cursor !== undefined && at !== undefined && cursor <= at;
};

/**
 * Edits every cached entry under `key` that has data; an entry nobody has read yet is left for its first read. An entry whose read
 * already answered at or past the event's `cursor` is left as it is.
 */
export function edit<T>(qc: QueryClient, key: Key, fn: (data: T) => T, cursor?: number): void {
  qc.setQueriesData<T>({ queryKey: key }, (data) => (data === undefined || inRead(data, cursor) ? data : fn(data)));
}

/** Sets an entry to what an event says, unless a read of it already answered at or past the event's `cursor`. */
export function put<T>(qc: QueryClient, key: Key, value: T, cursor: number | undefined): void {
  if (!inRead(qc.getQueryData(key), cursor)) qc.setQueryData<T>(key, value);
}

/** `list` with `item` in place of the entry `same` finds, or added at the end (or the start). */
export function upsert<T>(list: T[], item: T, same: (x: T) => boolean, at: 'end' | 'start' = 'end'): T[] {
  return list.some(same) ? list.map((x) => (same(x) ? item : x)) : at === 'end' ? [...list, item] : [item, ...list];
}

export const mapPages = <P>(data: Pages<P>, f: (page: P, index: number) => P): Pages<P> => ({ ...data, pages: data.pages.map(f) });

/** A negative id for a row the daemon has not numbered yet; each call gives a new one. */
let provisional = 0;
export const provisionalId = () => -(Date.now() * 1000 + (provisional++ % 1000));
