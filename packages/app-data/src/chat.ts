// Chat of one agent on the data layer: the person's messages and the agent's transcript as paged queries, and send as a write whose
// row shows at once and, if refused, stays with its text and a Retry.
import { useState } from 'react';
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { useStore } from '@handline/app-react';
import type { ChatMessage, TranscriptItem, Working } from '@handline/client';
import { mapPages, type Pages } from '#src/cache.ts';
import { NOT_WORKING, type MsgPage, type TrPage } from '#src/chat-events.ts';
import { useData, useFeed, useServes } from '#src/provider.ts';
import { qk } from '#src/reads.ts';
import { forgetRow, newOperationId, operationOf, type Delivery } from '#src/send-ops.ts';
import { useWrite } from '#src/write.ts';

export type ChatScope = 'mine' | 'all';
const PAGE = 30;

export interface Chat {
  /** off: the daemon does not serve chat; loading: nothing cached yet (draw a skeleton); error; ready. */
  status: 'off' | 'loading' | 'error' | 'ready';
  error: unknown;
  messages: ChatMessage[]; // oldest first
  items: TranscriptItem[]; // oldest first
  working: Working;
  hasOlder: boolean;
  loadingOlder: boolean;
  loadOlder: () => void;
  /** Reads both again (the error block's Retry). */
  refetch: () => void;
}

const byId = <T extends { id: number }>(lists: T[][]): T[] => [...new Map(lists.flat().map((x) => [x.id, x])).values()];

/** One agent's conversation, in the tab (`mine` or `all`) shown. */
export function useChat(agent: string, scope: ChatScope): Chat {
  const store = useStore();
  const { daemon } = useData();
  const m = useServes('chat.messages');
  const t = useServes('chat.transcript');
  const served = m.served && t.served;
  const on = served && m.open;
  useFeed('chat', served);
  const msgs = useInfiniteQuery({
    queryKey: qk.chatMessages(daemon, agent, scope),
    enabled: on,
    staleTime: Infinity,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => store.client.call('chat.messages', { agent, scope, before: pageParam, limit: PAGE }),
    getNextPageParam: (last: MsgPage) => (last.more && last.messages.length ? Math.min(...last.messages.map((x) => x.id)) : undefined),
  });
  const tr = useInfiniteQuery({
    queryKey: qk.transcript(daemon, agent),
    enabled: on,
    staleTime: Infinity,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => store.client.call('chat.transcript', { agent, before: pageParam, limit: PAGE }),
    getNextPageParam: (last: TrPage) => (last.more && last.items.length ? last.items[0]!.id : undefined),
  });
  return {
    status: !served ? 'off' : msgs.isError || tr.isError ? 'error' : msgs.isPending || tr.isPending ? 'loading' : 'ready',
    error: msgs.error ?? tr.error,
    // A withdrawn message of a structured agent was never written: it is not drawn.
    messages: byId((msgs.data?.pages ?? []).map((p) => p.messages))
      .filter((m) => !(m.receipt && m.landing === 'withdrawn'))
      .sort((a, b) => a.id - b.id),
    items: byId([...(tr.data?.pages ?? [])].reverse().map((p) => p.items)).sort((a, b) => a.id - b.id),
    working: tr.data?.pages[0]?.working ?? NOT_WORKING,
    hasOlder: !!msgs.hasNextPage || !!tr.hasNextPage,
    loadingOlder: msgs.isFetchingNextPage || tr.isFetchingNextPage,
    refetch: () => {
      void msgs.refetch();
      void tr.refetch();
    },
    loadOlder: () => {
      if (msgs.hasNextPage) void msgs.fetchNextPage();
      if (tr.hasNextPage) void tr.fetchNextPage();
    },
  };
}

/** The ready-made verbs of a conversation. A refused send stays as a `failed` row: Retry sends its text again, Discard drops it. */
export function useChatActions(agent: string) {
  const qc = useQueryClient();
  const { daemon } = useData();
  const send = useWrite('chat.send', agent);
  const sendNow = useWrite('chat.send_now', agent);
  const cancel = useWrite('chat.cancel', agent);
  const seen = useWrite('chat.seen', agent);
  const rows = (f: (m: ChatMessage) => boolean) => {
    for (const [key] of qc.getQueriesData<Pages<MsgPage>>({ queryKey: qk.chatMessagesOf(daemon, agent) }))
      qc.setQueryData<Pages<MsgPage>>(key, (d) => d && mapPages(d, (p) => ({ ...p, messages: p.messages.filter(f) })));
  };
  const failed = (id: number) =>
    qc
      .getQueriesData<Pages<MsgPage>>({ queryKey: qk.chatMessagesOf(daemon, agent) })
      .flatMap(([, d]) => d?.pages.flatMap((p) => p.messages) ?? [])
      .find((m) => m.id === id && m.state === 'failed');
  /** One operation id per message; a retry of a failed row sends the row's own. */
  const post = (
    to: string,
    body: string,
    parent: number | null,
    op = newOperationId(),
    delivery?: Delivery,
    attachments: string[] = [],
    parentItem?: number,
  ) =>
    send.mutate({
      agent: to,
      body,
      ...(parent != null ? { parent } : {}),
      ...(parentItem != null ? { parent_item: parentItem } : {}),
      ...(attachments.length ? { attachments } : {}),
      ...(delivery ? { delivery } : {}),
      request_id: op,
    });
  return {
    /** `parent` replies to a message, `parent_item` to one of the agent's own replies; `to` is another agent's chat. */
    send: (body: string, opts?: { parent?: number; parent_item?: number; to?: string; delivery?: Delivery; attachments?: string[] }) =>
      post(opts?.to ?? agent, body, opts?.parent ?? null, undefined, opts?.delivery, opts?.attachments, opts?.parent_item),
    retry: (id: number) => {
      const m = failed(id);
      if (!m) return;
      const was = operationOf(id);
      rows((x) => x.id !== id);
      forgetRow(id);
      post(
        m.to,
        m.body,
        m.parent,
        was?.op,
        was?.delivery,
        m.attachments?.map((a) => a.id),
        m.parent_item?.id,
      );
    },
    discard: (id: number) => {
      rows((x) => x.id !== id);
      forgetRow(id);
    },
    /** Writes a queued message to the agent now; its receipt goes to sent. */
    sendNow: (request: number) => sendNow.mutate({ request }),
    /** Takes the message back, delivered or not (a queued one of a structured agent leaves the conversation, with no confirm); one the agent already has needs `confirm`. Resolves when the daemon has done it, rejects when it refuses. */
    cancel: (request: number, confirm = false) => cancel.mutateAsync({ request, ...(confirm ? { confirm: true } : {}) }),
    /** The agent's messages up to this number are in front of the person: they leave the FOR YOU list. */
    seen: (up_to: number) => seen.mutate({ agent, up_to }),
    sending: send.isPending,
  };
}

export interface ChatWindow {
  /** The message the window was opened on; null while the live tail is shown. */
  target: number | null;
  messages: ChatMessage[]; // oldest first
  hasOlder: boolean;
  hasNewer: boolean;
  loading: boolean;
  /** Opens a window around a message, from what chat.locate returned. */
  open: (target: number, messages: ChatMessage[], moreOlder: boolean, moreNewer: boolean) => void;
  loadOlder: () => void;
  loadNewer: () => void;
  /** Back to the live tail. */
  close: () => void;
}

interface Held {
  target: number;
  messages: ChatMessage[];
  older: boolean;
  newer: boolean;
}

/** A stretch of one chat around an old message, read in both directions from it instead of paging up from the newest. `target` is null while the live tail is shown. */
export function useChatWindow(agent: string, scope: ChatScope): ChatWindow {
  const store = useStore();
  const [held, setHeld] = useState<Held | null>(null);
  const [loading, setLoading] = useState(false);
  const read = (h: Held, params: { before: number } | { since: number }, edge: 'older' | 'newer') => {
    setLoading(true);
    void store.client
      .call('chat.messages', { agent, scope, ...params, limit: PAGE })
      .then((r) =>
        setHeld((cur) => {
          if (!cur || cur.target !== h.target) return cur;
          return { ...cur, messages: byId([cur.messages, r.messages]).sort((a, b) => a.id - b.id), [edge]: r.more };
        }),
      )
      .finally(() => setLoading(false));
  };
  return {
    target: held?.target ?? null,
    messages: held?.messages ?? [],
    hasOlder: !!held?.older,
    hasNewer: !!held?.newer,
    loading,
    open: (target, messages, older, newer) => setHeld({ target, messages: [...messages].sort((a, b) => a.id - b.id), older, newer }),
    loadOlder: () => held && !loading && held.older && read(held, { before: Math.min(...held.messages.map((m) => m.id)) }, 'older'),
    loadNewer: () => held && !loading && held.newer && read(held, { since: Math.max(...held.messages.map((m) => m.id)) }, 'newer'),
    close: () => setHeld(null),
  };
}
