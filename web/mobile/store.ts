// The phone view of the shared store (@handline/app-core): the one state object views.ts draws.
import type { AgentDetail, AgentView, TerminalView } from '@handline/client';
import type { PageTab, Store } from '@handline/app-core';

export { KEY_STRIP } from '@handline/app-core';
export type Tab = PageTab;

export interface State {
  connection: Store['conn'];
  chat: boolean; // the daemon serves chat.*
  agents: AgentView[];
  page: { agent: string; detail: AgentDetail; tab: Tab; view: TerminalView } | null;
  error: string | null;
}

export function toState(s: Store): State {
  const detail = s.page && s.details.get(s.page.agent);
  return {
    connection: s.conn,
    chat: s.chat,
    agents: [...s.agents.values()],
    error: s.error,
    page: s.page && detail ? { ...s.page, detail } : null,
  };
}
