// What T<N>, #N and agent-name references need to be links, read once per chat so rows hold no data hook.
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useAgents, usePullRequests, useTasks } from '@handline/app-data';
import { useWorkspaceIfAny, type WorkspaceCtx } from '#src/Workspace.tsx';

export interface RefsData {
  /** Which task numbers exist: those up to the highest the task list holds. Nothing is a task until the list is in. */
  known: (id: number) => boolean;
  names: ReadonlySet<string>;
  /** The pull-request check, or undefined while the agent has no repo with one of that number to look up. */
  pr: ((n: number) => boolean) | undefined;
  repo: string | null;
  ws: WorkspaceCtx | null;
}

const Ctx = createContext<RefsData | null>(null);

/** The task list, the agents, the workspace and one pr.exists per `#N` of `text`. */
export function useRefsData(agent: string | undefined, text: string): RefsData {
  const ws = useWorkspaceIfAny();
  const top = Math.max(0, ...(useTasks({ all: true, limit: 200 }).data?.items ?? []).map((t) => t.id));
  const list = (useAgents().data?.agents ?? []).map((a) => a.name).join('\n');
  const names = useMemo(() => new Set(list ? list.split('\n') : []), [list]);
  const prs = usePullRequests(agent, text);
  return useMemo(
    () => ({ known: (id) => id <= top, names, pr: prs.repo ? prs.isPr : undefined, repo: prs.repo, ws }),
    [top, names, prs.repo, prs.isPr, ws],
  );
}

/** `text` is all the chat text whose `#N` is checked. */
export function ChatRefs({ agent, text, children }: { agent: string; text: string; children: ReactNode }) {
  return <Ctx.Provider value={useRefsData(agent, text)}>{children}</Ctx.Provider>;
}

/** The shared reads when a chat gave them, else null. */
export const useSharedRefs = (): RefsData | null => useContext(Ctx);
