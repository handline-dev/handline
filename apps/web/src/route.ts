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

/** Which of the page routes the URL names: `#/stats` or `#/settings`, or '' for neither. */
export function usePageRoute(): 'stats' | 'settings' | '' {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  return hash === '#/stats' ? 'stats' : hash === '#/settings' ? 'settings' : '';
}
