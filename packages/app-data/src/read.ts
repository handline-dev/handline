// The read hooks: `useRead` is the one place a read becomes a query, and every named hook is a line over it.
import { useCallback, useMemo, useState } from 'react';
import { keepPreviousData, useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useStore } from '@handline/app-react';
import type { CallParams, InboxItem, Methods } from '@handline/client';
import { useData, useFeed, useServes } from './provider.ts';
import { keyOf, reads, type ReadMethod, type ReadSpec } from './reads.ts';

export const SNAPSHOT_STALE_MS = 30_000;

type TimelineEntry = Methods['agent.timeline']['result']['items'][number];

export type ReadResult<M extends ReadMethod> = UseQueryResult<Methods[M]['result']>;

/**
 * A read of the daemon as a query. It runs only while the connection is open and the daemon lists the method; an event-fed read
 * is never stale (the bridge keeps it current) and a snapshot read is stale after 30 s. A list keeps its rows while a filter changes.
 */
export function useRead<M extends ReadMethod>(
  method: M,
  params: CallParams<M>,
  opts: { enabled?: boolean; refetchMs?: number } = {},
): ReadResult<M> {
  const store = useStore();
  const { daemon } = useData();
  const { served, open } = useServes(method);
  const spec: ReadSpec = reads[method];
  useFeed(spec.feed, served);
  return useQuery({
    queryKey: keyOf(daemon, method, params),
    queryFn: () => store.client.call(method, params),
    enabled: served && open && (opts.enabled ?? true),
    staleTime: spec.feed ? Infinity : SNAPSHOT_STALE_MS,
    refetchInterval: opts.refetchMs,
    placeholderData: spec.id ? undefined : keepPreviousData,
  });
}

export type ViewState = 'not_served' | 'loading' | 'error' | 'empty' | 'refreshing' | 'ready';

/** Which of a view's five states to draw (plus *ready*): the skeleton, the error block, the empty sentence, or the data with a quiet marker. */
export function viewState(
  q: Pick<UseQueryResult, 'isPending' | 'isFetching' | 'isError' | 'data'>,
  served: boolean,
  isEmpty = false,
): ViewState {
  if (!served) return 'not_served';
  if (q.isPending) return q.isError ? 'error' : 'loading';
  if (q.isError) return 'error';
  if (isEmpty) return 'empty';
  return q.isFetching ? 'refreshing' : 'ready';
}

export const useAgents = () => useRead('agents.list', {});
/**
 * Messages owed to each agent that have not reached it. The daemon sends `unreached` only for agents.list `status: true`, which is another
 * key from the event-fed list `useAgents` reads, so the bridge does not keep it current: it is read on its own, again every 20 s.
 */
export function useUnreached(): Map<string, number> {
  const q = useRead('agents.list', { status: true }, { refetchMs: 20_000 });
  return useMemo(
    () => new Map((q.data?.agents ?? []).flatMap((a) => (typeof a.unreached === 'number' ? [[a.name, a.unreached] as const] : []))),
    [q.data],
  );
}
export const useAgentDetail = (agent: string) => useRead('agent.get', { agent });
export const useAgentTimeline = (agent: string) => useRead('agent.timeline', { agent });

/**
 * An agent's timeline, newest first. The first page is the read the agents feed keeps current; *Older* pages are fetched with `before` and kept
 * until the agent changes, and `more` is the daemon's answer for the oldest page fetched.
 */
export function useTimeline(agent: string) {
  const first = useAgentTimeline(agent);
  const store = useStore();
  const [paged, setPaged] = useState<{ agent: string; items: TimelineEntry[]; more: boolean } | null>(null);
  const [state, setState] = useState<{ busy: boolean; error: unknown }>({ busy: false, error: null });
  const older = paged?.agent === agent ? paged : null;
  const items = useMemo(() => {
    const seen = new Set<number>();
    return [...(first.data?.items ?? []), ...(older?.items ?? [])]
      .filter((e) => !seen.has(e.id) && seen.add(e.id))
      .sort((a, b) => b.id - a.id);
  }, [first.data, older]);
  const more = older ? older.more : (first.data?.more ?? false);
  const loadOlder = useCallback(async () => {
    const before = items.at(-1)?.id;
    if (before === undefined) return;
    setState({ busy: true, error: null });
    try {
      const r = await store.client.call('agent.timeline', { agent, before });
      setPaged({ agent, items: [...(older?.items ?? []), ...r.items], more: r.more });
      setState({ busy: false, error: null });
    } catch (error) {
      setState({ busy: false, error });
    }
  }, [store, agent, items, older]);
  return { query: first, items, more, loadOlder, loadingOlder: state.busy, olderError: state.error };
}
export const useAgentOptions = () => useRead('agent.options', {});
export const useTriggers = (agent?: string) => useRead('agent.triggers', { agent });
export const usePrompts = (agent: string) => useRead('prompt.list', { agent });
export const useStatus = () => useRead('daemon.status', {});
export const useAccounts = () => useRead('accounts.list', {});
/** The accounts with what `hl account list` adds (plan, windows, can it take work and why): a read of its own, fetched again every 30 s because the accounts topic does not carry those fields. */
export const useAccountUsage = () => useRead('accounts.list', { usage: true, roster: true }, { refetchMs: SNAPSHOT_STALE_MS });
export const useInbox = (filter: CallParams<'inbox.list'> = {}) => useRead('inbox.list', filter);
/** The unread items of the whole inbox: the bell, the tab title and the unread dot of the page all read the one cached list, so a mark drops them together. */
export function useUnread(): InboxItem[] {
  const items = useInbox().data?.items;
  return useMemo(() => (items ?? []).filter((i) => !i.read), [items]);
}
export const useInboxItem = (id: number) => useRead('inbox.get', { id });
export const useTasks = (filter: CallParams<'tasks.list'> = {}) => useRead('tasks.list', filter);
/** What an agent holds and what it opened: two reads (the daemon pages tasks.list and filters by owner or by opener), as one query. */
export function useAgentTasks(agent: string) {
  const held = useTasks({ all: true, owner: agent, limit: 200 });
  const opened = useTasks({ all: true, from: agent, limit: 200 });
  const query = {
    isPending: held.isPending || opened.isPending,
    isFetching: held.isFetching || opened.isFetching,
    isError: held.isError || opened.isError,
    data: held.data && opened.data ? [held.data, opened.data] : undefined,
    error: held.error ?? opened.error,
    refetch: () => Promise.all([held.refetch(), opened.refetch()]),
  };
  const items = useMemo(() => [...(held.data?.items ?? []), ...(opened.data?.items ?? [])], [held.data, opened.data]);
  return { query, items, capped: !!(held.data?.more || opened.data?.more) };
}
export const useTask = (task: number) => useRead('task.get', { task });
export const useMemos = (task: number) => useRead('task.memo.list', { task });
export const useTaskBoard = () => useRead('task.board', {});
export const useTrust = () => useRead('trust.list', {});
export const useMemory = (agent?: string) => useRead('memory.status', { agent });
export const useTeam = () => useRead('team.get', {});
export const useHr = (filter: CallParams<'hr.list'> = {}) => useRead('hr.list', filter);
export const useRepos = () => useRead('repos.list', {});
export const useWorktrees = (filter: CallParams<'worktrees.list'> = {}) => useRead('worktrees.list', filter);
export const useWorktreeDiff = (path: string, file?: string) => useRead('worktree.diff', { path, file });
export const useSchedules = () => useRead('schedules.list', {});
export const useStats = (window: CallParams<'stats.get'>['window']) => useRead('stats.get', { window });
export const useOutlook = (range: CallParams<'stats.outlook'>['range']) => useRead('stats.outlook', { range });
export const useSettings = (agent?: string) => useRead('settings.get', { agent });
export const useAnswers = () => useRead('answers.list', {});
export const useDevices = () => useRead('devices.list', {});
export const useOperation = (operation: string) => useRead('operation.get', { operation });
/** Where a message number sits (its chat and scope); `enabled` false reads nothing until a message number is clicked. */
export const useChatLocate = (params: CallParams<'chat.locate'>, enabled = true) => useRead('chat.locate', params, { enabled });
export const useChatSearch = (params: CallParams<'chat.search'>) => useRead('chat.search', params);
