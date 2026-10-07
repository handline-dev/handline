import { useSyncExternalStore } from 'react';

const read = () => decodeURIComponent(location.hash.match(/^#\/agents\/(.+)$/)?.[1] ?? '');
const readTasks = () => location.hash.match(/^#\/tasks(?:\/(\d+))?$/)?.[1] ?? (/^#\/tasks\/?$/.test(location.hash) ? '' : null);
const subscribe = (cb: () => void) => {
  addEventListener('hashchange', cb);
  return () => removeEventListener('hashchange', cb);
};

/** The agent the URL names (`#/agents/:name`), or ''. */
export const useSelectedAgent = (): string => useSyncExternalStore(subscribe, read);
export const goAgent = (name: string): void => {
  location.hash = `#/agents/${encodeURIComponent(name)}`;
};

const INBOX = /^#\/inbox(?:\/(\d+))?$/;

/** The Inbox route: whether the URL is `#/inbox` or `#/inbox/:item`, and the item it names. */
export function useInboxRoute(): { open: boolean; item: number | null } {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  const m = INBOX.exec(hash);
  return { open: !!m, item: m?.[1] ? Number(m[1]) : null };
}
export const goInbox = (): void => {
  location.hash = '#/inbox';
};

/** The Tasks page the URL names: null off it, '' for the list, the task number (as text) for a detail. */
export const useTasksRoute = (): string | null => useSyncExternalStore(subscribe, readTasks);
export const goTasks = (task?: number): void => {
  location.hash = task === undefined ? '#/tasks' : `#/tasks/${task}`;
};
