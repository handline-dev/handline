import { useSyncExternalStore } from 'react';

const read = () => location.hash.match(/^#\/agents\/(.+?)(\/settings)?$/);
const subscribe = (cb: () => void) => {
  addEventListener('hashchange', cb);
  return () => removeEventListener('hashchange', cb);
};
const hash = () => location.hash;

/** The agent the URL names (`#/agents/:name`, or `#/agents/:name/settings`), or ''. */
export const useSelectedAgent = (): string => {
  useSyncExternalStore(subscribe, hash);
  return decodeURIComponent(read()?.[1] ?? '');
};
/** Whether the URL is the agent's settings page. */
export const useSettingsOpen = (): boolean => {
  useSyncExternalStore(subscribe, hash);
  return !!read()?.[2];
};
export const goAgent = (name: string): void => {
  location.hash = `#/agents/${encodeURIComponent(name)}`;
};
export const goSettings = (name: string): void => {
  location.hash = `#/agents/${encodeURIComponent(name)}/settings`;
};
