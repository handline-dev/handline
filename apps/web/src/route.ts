import { useSyncExternalStore } from 'react';
import { DEBUG_HASH } from '#src/debug/paneFacts.ts';

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
      history.replaceState(history.state as object | null, '', location.hash.replace(/\?m=\d+$/, ''));
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

/** Which of the page routes the URL names: `#/stats`, `#/settings`, `#/my-turn` or `#/admin`, or '' for neither. */
export function usePageRoute(): 'stats' | 'settings' | 'my-turn' | 'admin' | '' {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  return hash === '#/stats' ? 'stats' : hash === '#/settings' ? 'settings' : hash === '#/my-turn' ? 'my-turn' : hash === '#/admin' ? 'admin' : '';
}

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

let wantsTerminal: string | null = null;
/** Opens the agent's page with its terminal (a structured agent's transcript) in front: the page takes the wish on load, or on this event. */
export function goTerminal(name: string): void {
  wantsTerminal = name;
  goAgent(name);
  dispatchEvent(new Event('handline-terminal'));
}
/** True once for the agent a `goTerminal` named. */
export function takeTerminalWish(agent: string): boolean {
  if (wantsTerminal !== agent) return false;
  wantsTerminal = null;
  return true;
}

const PUSHED = 'handline-history';
const subscribeState = (cb: () => void) => {
  addEventListener('popstate', cb);
  addEventListener(PUSHED, cb);
  return () => {
    removeEventListener('popstate', cb);
    removeEventListener(PUSHED, cb);
  };
};
/** A choice made inside a page (a tab) that rides in the history entry: setting it adds an entry, Back and Forward bring the earlier value. */
/** Puts a value into the current entry without adding one (the entry a page opened with). */
export function recordHistoryValue(key: string, v: string): void {
  history.replaceState({ ...(history.state as object | null), [key]: v }, '');
  dispatchEvent(new Event(PUSHED));
}
export function useHistoryValue(key: string): [string | undefined, (v: string) => void] {
  const v = useSyncExternalStore(subscribeState, () => (history.state as Record<string, string> | null)?.[key]);
  return [
    v,
    (next) => {
      if (next === v) return;
      history.pushState({ ...(history.state as object | null), [key]: next }, '');
      dispatchEvent(new Event(PUSHED));
    },
  ];
}
