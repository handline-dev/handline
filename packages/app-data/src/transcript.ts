// The transcript of one structured session as one cached entry: the newest page of transcript.events, which the `transcript` topic
// extends by seq. A hole, a reconnect or a gap read the entry again from the first missing seq; a view needs no terminal to watch.
import { useMemo, useState } from 'react';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useStore } from '@handline/app-react';
import type { Event as WireEvent, TranscriptEvent } from '@handline/client';
import { edit, type Ctx } from './cache.ts';
import { classify } from './errors.ts';
import { useData, useFeed, useServes } from './provider.ts';
import { qk } from './reads.ts';
import { EMPTY, firstHole, join, lastSeq, merge, toTurns, type Log, type Turn } from './transcript-model.ts';

const PAGE = 200;

/** What the entry holds: the events by seq and the oldest seq the daemon said it keeps, from the last read. */
export interface TranscriptData {
  log: Log;
  floor: number;
}

/** Events that arrive while the first read is out; applied above it. */
const early = new WeakMap<QueryClient, Map<string, TranscriptEvent[]>>();
const bufferOf = (qc: QueryClient) => early.get(qc) ?? early.set(qc, new Map()).get(qc)!;

/** transcript.event: the next seq is added, one already held is dropped, a later one leaves a hole and the entry is read again. */
export function transcriptEvent({ qc, d }: Ctx, e: WireEvent): void {
  const ev = e.data as unknown as TranscriptEvent;
  // A card opens or closes: the prompt list, which says whether the daemon still holds it, is read again.
  if (ev.kind === 'permission' || ev.kind === 'permission_answer') void qc.invalidateQueries({ queryKey: qk.prompts(d, ev.agent) });
  const key = qk.transcriptEvents(d, ev.agent);
  const held = qc.getQueryData<TranscriptData>(key);
  if (!held) {
    if (qc.isFetching({ queryKey: key })) bufferOf(qc).set(key.join('/'), [...(bufferOf(qc).get(key.join('/')) ?? []), ev]);
    return;
  }
  const last = lastSeq(held.log);
  if (ev.seq <= last) return;
  edit<TranscriptData>(qc, key, (t) => ({ ...t, log: merge(t.log, [ev]) }));
  if (ev.seq > last + 1) void qc.invalidateQueries({ queryKey: key, exact: true });
}

export interface Transcript {
  /** terminal: the read was refused as unsupported (a terminal agent, or a daemon without the transcript); views choose by AgentView.structured and keep this as the fallback. */
  status: 'terminal' | 'loading' | 'error' | 'ready';
  error: unknown;
  /** Oldest first. */
  turns: Turn[];
  /** The newest turn has begun and not ended. */
  running: boolean;
  hasOlder: boolean;
  loadingOlder: boolean;
  /** The last older page failed to load; cleared by the next try. */
  olderError: unknown;
  loadOlder: () => void;
  /** Reads again from the first missing seq (the error block's Retry). */
  refetch: () => void;
}

/** One agent's transcript. */
export function useTranscript(agent: string): Transcript {
  const store = useStore();
  const qc = useQueryClient();
  const { daemon } = useData();
  const { served, open } = useServes('transcript.events');
  useFeed('transcript', served);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<unknown>(null);
  const key = qk.transcriptEvents(daemon, agent);
  const read = (p: { since_seq?: number; before_seq?: number }) => store.client.call('transcript.events', { agent, limit: PAGE, ...p });

  const q = useQuery({
    queryKey: qk.transcriptEvents(daemon, agent),
    enabled: served && open,
    staleTime: Infinity,
    retry: false,
    queryFn: async ({ client, queryKey }): Promise<TranscriptData> => {
      let cur = client.getQueryData<TranscriptData>(queryKey);
      if (!cur) {
        const bufferKey = queryKey.join('/');
        bufferOf(client).delete(bufferKey);
        const r = await read({});
        cur = { log: merge(merge(EMPTY, r.events), bufferOf(client).get(bufferKey) ?? []), floor: r.first_seq };
        bufferOf(client).delete(bufferKey);
        if (firstHole(cur.log) === null) return cur;
      }
      // From the first missing seq, while the daemon has more and a read still adds something.
      for (;;) {
        const since = firstHole(cur.log) ?? lastSeq(cur.log);
        const r = await read({ since_seq: since });
        // What lay after `since` is gone: the break goes before the first event after it, whether held already or just read.
        const next = Math.min(cur.log.events.find((e) => e.seq > since)?.seq ?? Infinity, r.events[0]?.seq ?? Infinity);
        const brk = r.gap && cur.log.events.length && next < Infinity ? next : undefined;
        const log = merge(cur.log, r.events, brk);
        const grew = log !== cur.log;
        cur = { log, floor: r.gap ? r.first_seq : cur.floor };
        if (!r.more || !grew) break;
      }
      // The bridge added events to the entry while the reads were out: this result must not take them away.
      const now = client.getQueryData<TranscriptData>(queryKey);
      return now ? { ...cur, log: join(cur.log, now.log) } : cur;
    },
  });

  const data = q.data;
  const turns = useMemo(() => toTurns(data?.log ?? EMPTY), [data?.log]);
  const first = data?.log.events[0]?.seq ?? 0;
  const refused = q.isError && classify(q.error).kind === 'not_served';
  return {
    status: !served || refused ? 'terminal' : data ? 'ready' : q.isError ? 'error' : 'loading',
    error: q.error,
    turns,
    running: turns.at(-1)?.running ?? false,
    hasOlder: !!data && first > Math.max(data.floor, 1),
    loadingOlder,
    olderError,
    loadOlder: () => {
      if (!first || loadingOlder) return;
      setLoadingOlder(true);
      setOlderError(null);
      read({ before_seq: first })
        .then((older) => edit<TranscriptData>(qc, key, (t) => ({ log: merge(t.log, older.events), floor: older.first_seq })))
        .catch(setOlderError)
        .finally(() => setLoadingOlder(false));
    },
    refetch: () => void q.refetch(),
  };
}
