// The tabs opened inside one agent's page, beside its Chat or Terminal, which cannot be closed: a file, a task, the Tasks list. Back closes the last one opened.
import { createContext, useContext } from 'react';
import { useHistoryValue } from '#src/history.tsx';

export type AgentTab = { id: string; kind: 'file' | 'task' | 'tasks'; path?: string; task?: number };

type State = { tabs: AgentTab[]; active: string | null };

export type AgentTabs = State & {
  open: (tab: AgentTab) => void;
  close: (id: string) => void;
  /** null is the page's own Chat or Terminal. */
  select: (id: string | null) => void;
};

const NONE: State = { tabs: [], active: null };

export const fileTab = (path: string): AgentTab => ({ id: `file:${path}`, kind: 'file', path });
export const taskTab = (task: number): AgentTab => ({ id: `task:${task}`, kind: 'task', task });

export function useAgentTabs(agent: string): AgentTabs {
  const [state, set] = useHistoryValue<State>(`agent.tabs:${agent}`, NONE);
  return {
    ...state,
    open: (tab) => set({ tabs: state.tabs.some((t) => t.id === tab.id) ? state.tabs : [...state.tabs, tab], active: tab.id }),
    close: (id) => set({ tabs: state.tabs.filter((t) => t.id !== id), active: state.active === id ? null : state.active }),
    select: (id) => set({ ...state, active: id }),
  };
}

export const AgentTabsContext = createContext<AgentTabs | null>(null);
export const useOpenInTab = () => useContext(AgentTabsContext);

export function tabLabel(t: AgentTab): string {
  if (t.kind === 'file') return t.path ? t.path.split('/').at(-1)! : 'File';
  if (t.kind === 'task') return `T${t.task}`;
  return 'Tasks';
}
