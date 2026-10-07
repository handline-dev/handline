import { useSyncExternalStore } from 'react';

const read = () => decodeURIComponent(location.hash.match(/^#\/agents\/(.+)$/)?.[1] ?? '');
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
