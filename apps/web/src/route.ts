import { useSyncExternalStore } from 'react';
import { DEBUG_HASH } from './debug/paneFacts.ts';

const read = () => location.hash.match(/^#\/agents\/(.+?)(\/settings)?(?:\?m=(\d+))?$/);
const readTasks = () => location.hash.match(/^#\/tasks(?:\/(\d+|graph))?$/)?.[1] ?? (/^#\/tasks\/?$/.test(location.hash) ? '' : null);
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
export const goTasks = (task?: number | 'graph'): void => {
  location.hash = task === undefined ? '#/tasks' : `#/tasks/${task}`;
};
/** Opens the agent's chat on this message: the number rides in the URL (`#/agents/:name?m=:id`) until the Chat tab has scrolled to it. */
export const goMessage = (agent: string, id: number): void => {
  location.hash = `#/agents/${encodeURIComponent(agent)}?m=${id}`;
};
/** The message number the URL asks this agent's chat to show; `done` takes it out of the URL once the chat has taken it. */
export function useWantedMessage(agent: string): { id: number | null; done: () => void } {
  useSyncExternalStore(subscribe, hash);
  const m = read();
  return {
    id: m?.[3] && decodeURIComponent(m[1]!) === agent ? Number(m[3]) : null,
    done: () => {
      history.replaceState(null, '', location.hash.replace(/\?m=\d+$/, ''));
      dispatchEvent(new Event('hashchange'));
    },
  };
}
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

/** Whether the URL is the debug window's (`#/debug/terminals`), which Settings opens in a window of its own. */
export function useDebugRoute(): boolean {
  return useSyncExternalStore(subscribe, () => location.hash) === DEBUG_HASH;
}
