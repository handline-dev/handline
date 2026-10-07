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

const hash = () => location.hash;
const teamAt = () => location.hash.match(/^#\/team(?:\/(hr)(?:\/(\d+))?)?$/);
/** `#/team`, `#/team/hr` or `#/team/hr/:proposal`; null for any other page. */
export const useTeamRoute = (): { tab: 'team' | 'hr'; proposal: number | null } | null => {
  useSyncExternalStore(subscribe, hash);
  const m = teamAt();
  return m ? { tab: m[1] ? 'hr' : 'team', proposal: m[2] ? Number(m[2]) : null } : null;
};
export const goTeam = (tab: 'team' | 'hr' = 'team'): void => {
  location.hash = tab === 'hr' ? '#/team/hr' : '#/team';
};
