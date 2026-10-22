// What a notice-raising event did while the connection was down: counted once on reconnect, shown as one line, never replayed as toasts.
import { useCallback } from 'react';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import type { DaemonClient } from '@handline/client';
import { useData } from '#src/provider.ts';

export interface Away {
  count: number;
  /** The daemon kept no more than this many (or dropped older ones): the count is a floor. */
  more: boolean;
}

const LIMIT = 100;
const awayKey = (d: string) => ['away', d] as const;

/** Reads the notices after `from` once and keeps their count under its own key (a resync of the daemon's reads leaves it alone). A failed read leaves no line. */
export async function readAway(client: DaemonClient, qc: QueryClient, d: string, from: number | undefined): Promise<void> {
  if (from === undefined) return;
  const feed = await client.call('events.feed', { since: from, topics: ['notifications', 'hr'], limit: LIMIT }).catch(() => null);
  if (feed && feed.events.length)
    qc.setQueryData<Away>(awayKey(d), { count: feed.events.length, more: feed.events.length >= LIMIT || feed.gap });
}

/** The notices missed while away, or null; `dismiss` clears the line. */
export function useAway(): { away: Away | null; dismiss: () => void } {
  const qc = useQueryClient();
  const { daemon } = useData();
  const { data } = useQuery({ queryKey: awayKey(daemon), queryFn: () => null as Away | null, enabled: false, staleTime: Infinity });
  const dismiss = useCallback(() => qc.setQueryData(awayKey(daemon), null), [qc, daemon]);
  return { away: data ?? null, dismiss };
}

/** The one line: "3 notices while you were away" (a floor when `more`). */
export const awayLine = (a: Away): string =>
  `${a.count}${a.more ? '+' : ''} ${a.count === 1 && !a.more ? 'notice' : 'notices'} while you were away`;
