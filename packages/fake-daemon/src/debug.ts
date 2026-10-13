// The fake daemon's debug.terminals: one pane per agent that has a terminal, and one shell pane no agent has.
import type { Methods } from '@handline/contract';
import type { Call } from './handlers.ts';
import { tid } from './session.ts';

type Pane = Methods['debug.terminals']['result']['panes'][number];

/** The pane the fake lists that no agent has, as a leftover shell would be. */
export const STRAY_PANE = '%stray';

const blank = (terminal: string, window: number): Pane => ({
  session: 'ocp',
  window: String(window),
  window_name: '',
  terminal,
  pid: null,
  command: 'zsh',
  path: null,
  dead: false,
  last_activity: null,
  idle_s: null,
  process: 'shell',
  agent_process: null,
  agent: null,
  orphan: true,
  closed: false,
  recorded_agent: null,
  state: null,
  account: null,
  session_id: null,
  worktree: null,
  cache_until: null,
  orca_terminal: null,
});

export const DEBUG_HANDLERS: Record<string, (h: Call) => unknown> = {
  'debug.terminals': ({ script, iso }): Methods['debug.terminals']['result'] => {
    const agents = [...script.sessions.values()].filter((s) => s.hasTerminal && !s.structured);
    const panes = agents.map(
      (s, i): Pane => ({
        ...blank(tid(s), i),
        window_name: s.fx.name,
        pid: 1000 + i,
        command: s.open ? 'claude' : 'zsh',
        path: s.fx.worktree,
        last_activity: s.since,
        idle_s: 0,
        process: s.open ? 'agent' : 'shell',
        agent_process: s.open ? { pid: 2000 + i, name: 'claude' } : null,
        agent: s.fx.name,
        orphan: false,
        state: s.state,
        account: s.fx.account,
        session_id: s.sessionId,
        worktree: s.fx.worktree,
      }),
    );
    return { host: 'fake', now: iso(), panes: [...panes, { ...blank(STRAY_PANE, panes.length), recorded_agent: agents[0]?.fx.name ?? null }] };
  },
};
