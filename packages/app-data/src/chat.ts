// Chat of one agent on the data layer: the person's messages and the agent's transcript as paged queries, and send as a write whose
// row shows at once and, if refused, stays with its text and a Retry.
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { useStore } from '@handline/app-react';
import type { ChatMessage, TranscriptItem, Working } from '@handline/client';
import { mapPages, type Pages } from './cache.ts';
import { NOT_WORKING, type MsgPage, type TrPage } from './chat-events.ts';
import { useData, useFeed, useServes } from './provider.ts';
import { qk } from './reads.ts';
import { useWrite } from './write.ts';

export type ChatScope = 'mine' | 'all';
const PAGE = 50;

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
    messages: byId((msgs.data?.pages ?? []).map((p) => p.messages)).sort((a, b) => a.id - b.id),
    items: byId([...(tr.data?.pages ?? [])].reverse().map((p) => p.items)).sort((a, b) => a.id - b.id),
    working: tr.data?.pages[0]?.working ?? NOT_WORKING,
    hasOlder: !!msgs.hasNextPage || !!tr.hasNextPage,
    loadingOlder: msgs.isFetchingNextPage || tr.isFetchingNextPage,
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
  const withdraw = useWrite('chat.withdraw', agent);
  const dismiss = useWrite('chat.dismiss', agent);
  const answer = useWrite('chat.answer', agent);
  const rows = (f: (m: ChatMessage) => boolean) => {
    for (const [key] of qc.getQueriesData<Pages<MsgPage>>({ queryKey: qk.chatMessagesOf(daemon, agent) }))
      qc.setQueryData<Pages<MsgPage>>(key, (d) => d && mapPages(d, (p) => ({ ...p, messages: p.messages.filter(f) })));
  };
  const failed = (id: number) =>
    qc
      .getQueriesData<Pages<MsgPage>>({ queryKey: qk.chatMessagesOf(daemon, agent) })
      .flatMap(([, d]) => d?.pages.flatMap((p) => p.messages) ?? [])
      .find((m) => m.id === id && m.state === 'failed');
  return {
    send: (body: string) => send.mutate({ agent, body }),
    retry: (id: number) => {
      const m = failed(id);
      if (!m) return;
      rows((x) => x.id !== id);
      send.mutate({ agent, body: m.body });
    },
    discard: (id: number) => rows((x) => x.id !== id),
    withdraw: (request: number) => withdraw.mutate({ request }),
    dismiss: (request: number) => dismiss.mutate({ request }),
    answer: (request: number, text: string) => answer.mutate({ request, text }),
    sending: send.isPending,
  };
}
