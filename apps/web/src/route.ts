import { useSyncExternalStore } from 'react';

const read = () => location.hash.match(/^#\/agents\/(.+?)(\/settings)?$/);
const readTasks = () => location.hash.match(/^#\/tasks(?:\/(\d+))?$/)?.[1] ?? (/^#\/tasks\/?$/.test(location.hash) ? '' : null);
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
export const goSettings = (name: string): void => {
  location.hash = `#/agents/${encodeURIComponent(name)}/settings`;
};

const ACCOUNTS = /^#\/accounts(?:\/(.+))?$/;

/** The Accounts route: whether the URL is `#/accounts` or `#/accounts/:name`, and the account it names. */
export function useAccountsRoute(): { open: boolean; name: string | null } {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  const m = ACCOUNTS.exec(hash);
  return { open: !!m, name: m?.[1] ? decodeURIComponent(m[1]) : null };
}

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

/** Which of the page routes the URL names: `#/stats` or `#/settings`, or '' for neither. */
export function usePageRoute(): 'stats' | 'settings' | '' {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  return hash === '#/stats' ? 'stats' : hash === '#/settings' ? 'settings' : '';
}

const WORKTREES = /^#\/worktrees(?:\/(.+))?$/;
/** The Worktrees route: whether the URL is `#/worktrees` or `#/worktrees/:path`, and the worktree path it names. */
export function useWorktreesRoute(): { open: boolean; path: string | null } {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  const m = WORKTREES.exec(hash);
  return { open: !!m, path: m?.[1] ? decodeURIComponent(m[1]) : null };
}
export const goWorktrees = (path?: string): void => {
  location.hash = path === undefined ? '#/worktrees' : `#/worktrees/${encodeURIComponent(path)}`;
};

const NEW_AGENT = /^#\/new(?:\/(.+))?$/;
/** The New agent route: whether the URL is `#/new` or `#/new/:operation`, and the operation it names. */
export function useNewAgentRoute(): { open: boolean; operation: string | null } {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  const m = NEW_AGENT.exec(hash);
  return { open: !!m, operation: m?.[1] ? decodeURIComponent(m[1]) : null };
}
export const goNew = (operation?: string): void => {
  location.hash = operation === undefined ? '#/new' : `#/new/${encodeURIComponent(operation)}`;
};
