// The read hooks: `useRead` is the one place a read becomes a query, and every named hook is a line over it.
import { useCallback, useMemo, useState } from 'react';
import { keepPreviousData, useQueries, useQuery, type UseQueryResult } from '@tanstack/react-query';
import { hashNumbers, reviewPictures, waitingCount } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { CallParams, InboxItem, Methods, Task } from '@handline/client';
import { useData, useFeed, useServes } from '#src/provider.ts';
import { keyOf, reads, type ReadMethod, type ReadSpec } from '#src/reads.ts';

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
 * An agent's timeline, newest first: the first page the agents feed keeps current, plus *Older* pages fetched with `before`. Older pages hold the
 * ids below `base`; once new events move the first page past `base` they would leave a gap, so they are dropped.
 */
export function useTimeline(agent: string) {
  const first = useAgentTimeline(agent);
  const store = useStore();
  const [paged, setPaged] = useState<{ agent: string; base: number; items: TimelineEntry[]; more: boolean } | null>(null);
  const [state, setState] = useState<{ busy: boolean; error: unknown }>({ busy: false, error: null });
  const top = first.data ? Math.min(...first.data.items.map((e) => e.id)) : 0;
  const older = paged?.agent === agent && (!first.data?.more || top <= paged.base) ? paged : null;
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
      setPaged({ agent, base: older?.base ?? before, items: [...(older?.items ?? []), ...r.items], more: r.more });
      setState({ busy: false, error: null });
    } catch (error) {
      setState({ busy: false, error });
    }
  }, [store, agent, items, older]);
  return { query: first, items, more, loadOlder, loadingOlder: state.busy, olderError: state.error };
}
/** Which `#N` of a text are pull requests of the agent's repo, and where that repo is: a text with none asks nothing, a daemon without pr.exists or an agent without a GitHub repo finds none. */
export function usePullRequests(agent: string | undefined, text: string): { repo: string | null; isPr: (n: number) => boolean } {
  const numbers = agent ? hashNumbers(text).sort((a, b) => a - b) : [];
  const { data } = useRead('pr.exists', { agent: agent ?? '', numbers }, { enabled: numbers.length > 0 });
  const found = new Set(data?.found ?? []);
  return { repo: data?.repo ?? null, isPr: (n) => found.has(n) };
}
export const useAgentOptions = () => useRead('agent.options', {});
export const useTriggers = (agent?: string) => useRead('agent.triggers', { agent });
export const usePrompts = (agent: string) => useRead('prompt.list', { agent });
export const useStatus = () => useRead('daemon.status', {});
export const useAccounts = () => useRead('accounts.list', {});
/** The accounts with what `hl account list` adds (plan, windows, can it take work and why): a read of its own, fetched again every 30 s because the accounts topic does not carry those fields. */
export const useAccountUsage = () => useRead('accounts.list', { usage: true, roster: true }, { refetchMs: SNAPSHOT_STALE_MS });
export const useInbox = (filter: CallParams<'inbox.list'> = {}) => useRead('inbox.list', filter);
/** The inbox page the feed keeps current plus the older pages fetched with `before` as the end of the list comes near; an item of an older page is not touched by the feed. */
export function useInboxPages() {
  const first = useInbox();
  const store = useStore();
  const [older, setOlder] = useState<{ items: InboxItem[]; more: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const head = first.data?.items;
  const items = useMemo(() => {
    const seen = new Set<number>();
    return [...(head ?? []), ...(older?.items ?? [])].filter((i) => !seen.has(i.id) && seen.add(i.id)).sort((a, b) => b.id - a.id);
  }, [head, older]);
  const more = older ? older.more : (first.data?.more ?? false);
  const loadOlder = useCallback(async () => {
    const before = items.at(-1)?.id;
    if (before === undefined || busy) return;
    setBusy(true);
    try {
      const r = await store.client.call('inbox.list', { before });
      setOlder((o) => ({ items: [...(o?.items ?? []), ...r.items], more: r.more }));
    } finally {
      setBusy(false);
    }
  }, [store, items, busy]);
  return { query: first, items, more, loadOlder, loadingOlder: busy };
}
/** The unread items of the whole inbox: the bell, the tab title and the unread dot of the page all read the one cached list, so a mark drops them together. */
export function useUnread(): InboxItem[] {
  const items = useInbox().data?.items;
  return useMemo(() => (items ?? []).filter((i) => !i.read), [items]);
}
export const useInboxItem = (id: number) => useRead('inbox.get', { id });
/** The tasks an agent owns that are not done, read only when asked for (a row's list is opened). */
export const useOpenTasks = (agent: string, enabled: boolean) => useRead('tasks.list', { owner: agent }, { enabled });
export const useTasks = (filter: CallParams<'tasks.list'> = {}) => useRead('tasks.list', filter);
/** How many tasks a list shows at first and each time "Show older" is pressed. */
export const TASK_PAGE = 100;

/** The newest page of a task list, kept current by the feed, and the older pages read with `before` when asked; an older row is not touched by the feed. */
export function usePagedTasks(filter: CallParams<'tasks.list'>) {
  const first = useTasks({ ...filter, limit: TASK_PAGE });
  const store = useStore();
  const key = JSON.stringify(filter);
  const [older, setOlder] = useState<{ key: string; items: Task[]; more: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const mine = older?.key === key ? older : null; // a changed filter starts over
  const head = first.data?.items;
  const items = useMemo(() => {
    const seen = new Set<number>();
    return [...(head ?? []), ...(mine?.items ?? [])].filter((t) => !seen.has(t.id) && seen.add(t.id)).sort((a, b) => b.id - a.id);
  }, [head, mine]);
  const more = mine ? mine.more : (first.data?.more ?? false);
  const loadOlder = useCallback(async () => {
    const before = items.at(-1)?.id;
    if (before === undefined || busy) return;
    setBusy(true);
    try {
      const r = await store.client.call('tasks.list', { ...filter, limit: TASK_PAGE, before });
      setOlder((o) => ({ key, items: [...(o?.key === key ? o.items : []), ...r.items], more: r.more }));
    } finally {
      setBusy(false);
    }
  }, [store, items, busy, filter, key]);
  return { query: first, items, more, loadOlder, loadingOlder: busy };
}
/** What an agent holds and what it opened: two paged reads (the daemon pages tasks.list and filters by owner or by opener), as one list. */
export function useAgentTasks(agent: string) {
  const held = usePagedTasks({ all: true, owner: agent });
  const opened = usePagedTasks({ all: true, from: agent });
  const query = {
    isPending: held.query.isPending || opened.query.isPending,
    isFetching: held.query.isFetching || opened.query.isFetching,
    isError: held.query.isError || opened.query.isError,
    data: held.query.data && opened.query.data ? [held.query.data, opened.query.data] : undefined,
    error: held.query.error ?? opened.query.error,
    refetch: () => Promise.all([held.query.refetch(), opened.query.refetch()]),
  };
  const items = useMemo(() => [...held.items, ...opened.items], [held.items, opened.items]);
  const more = held.more || opened.more;
  const loadOlder = useCallback(async () => {
    await Promise.all([held.more ? held.loadOlder() : null, opened.more ? opened.loadOlder() : null]);
  }, [held, opened]);
  return { query, items, more, loadOlder, loadingOlder: held.loadingOlder || opened.loadingOlder };
}
export const useAgentStrip = (agent: string) => useRead('agent.strip', { agent }, { enabled: agent !== '' });
export const useTask = (task: number) => useRead('task.get', { task });
/** The titles of these tasks, each read by its id (the same read, and cache, as `useTask`): for a task the list of the newest tasks no longer holds. */
export const useTaskTitles = (ids: readonly number[]): Map<number, string> => useTaskTitleReads(ids).titles;
/** The same, and whether any of the reads is still on its way. */
export function useTaskTitleReads(ids: readonly number[]): { titles: Map<number, string>; pending: boolean } {
  const store = useStore();
  const { daemon } = useData();
  const { served, open } = useServes('task.get');
  const results = useQueries({
    queries: ids.map((task) => ({
      queryKey: keyOf(daemon, 'task.get', { task }),
      queryFn: () => store.client.call('task.get', { task }),
      enabled: served && open,
      staleTime: SNAPSHOT_STALE_MS,
    })),
  });
  return {
    titles: new Map(results.flatMap((r) => (r.data ? [[r.data.id, r.data.title] as const] : []))),
    pending: results.some((r) => r.isPending && r.fetchStatus !== 'idle'),
  };
}
export const useTaskHistory = (task: number) => useRead('task.history', { task });
export const useTaskTime = (task: number) => useRead('task.time', { task });
export const useMemos = (task: number) => useRead('task.comment.list', { task });
/** A review's pictures; empty while the comments load or when the first comment has none. */
export const useReviewPictures = (task: number) => reviewPictures(useMemos(task).data?.memos ?? []);
export const useTaskBoard = () => useRead('task.board', {});
export const useTrust = () => useRead('trust.list', {});
/** The newest events of the daemon's log, read once when the page opens (the log is not a cache the bridge keeps; a screen that wants it live asks again). */
export function useRecentEvents(limit: number) {
  const store = useStore();
  const { daemon } = useData();
  const { served, open } = useServes('events.feed');
  return useQuery({
    queryKey: [daemon, 'events.feed', limit],
    queryFn: () => store.client.call('events.feed', { limit }),
    enabled: served && open,
    gcTime: 0,
    select: (r) => [...r.events].sort((a, b) => b.cursor - a.cursor),
  });
}
export const useMemory = (agent?: string) => useRead('memory.status', { agent });
export const useTeam = () => useRead('team.get', {});
export const useHr = (filter: CallParams<'hr.list'> = {}) => useRead('hr.list', filter);
/** How many proposals wait on the founder; 0 while the list is not here or not served. */
export const useHrCount = (): number => waitingCount(useHr().data?.proposals ?? []);
export const usePrefs = () => useRead('prefs.get', {});
export const useRepos = () => useRead('repos.list', {});
export const useWorktrees = (filter: CallParams<'worktrees.list'> = {}) => useRead('worktrees.list', filter);
export const useFile = (agent: string, path: string) => useRead('file.read', { agent, path }, { enabled: path !== '' });
export const useSchedules = () => useRead('schedules.list', {});
export const useStats = (window: CallParams<'stats.get'>['window']) => useRead('stats.get', { window });
export const useOutlook = (range: CallParams<'stats.outlook'>['range']) => useRead('stats.outlook', { range });
export const useSettings = (agent?: string) => useRead('settings.get', { agent });
export const useConnection = () => useRead('connection.get', {});
export const useAnswers = () => useRead('answers.list', {});
export const useDevices = () => useRead('devices.list', {});
export const useOperation = (operation: string) => useRead('operation.get', { operation });
/** Where a message number sits (its chat and scope); `enabled` false reads nothing until a message number is clicked. */
export const useChatLocate = (params: CallParams<'chat.locate'>, enabled = true) => useRead('chat.locate', params, { enabled });
export const useChatRefs = (agent: string, enabled = true) => useRead('chat.refs', { agent }, { enabled });
export const useChatSearch = (params: CallParams<'chat.search'>, enabled = true) => useRead('chat.search', params, { enabled });

/** A one-off load that is not a daemon read (the desktop shell's answer, a picture's bytes) as a query: kept per `key`, undefined until it lands or when `enabled` is off. */
export function useLoaded<T>(key: readonly unknown[], load: () => Promise<T>, enabled = true): T | undefined {
  return useQuery({ queryKey: ['loaded', ...key], queryFn: load, enabled, staleTime: Infinity }).data;
}
