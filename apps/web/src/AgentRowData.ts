// What an agent row reads from the data layer, in one place, so the row's view holds no data hook.
import { queuedBehind, waitingTasks } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useMe } from '@handline/app-react';
import { useOpenTasks, useVerbMark } from '@handline/app-data';

export interface AgentRowData {
  me: string;
  mark: ReturnType<typeof useVerbMark>;
  waiting: { id: number; title: string }[];
}

/** `hot`: the pointer or focus is on the row, the only time the open tasks behind its current one are read. */
export function useAgentRowData(a: AgentView, hot: boolean): AgentRowData {
  const me = useMe();
  const mark = useVerbMark(a.name);
  const items = useOpenTasks(a.name, hot && (queuedBehind(a) || 0) > 0).data?.items;
  return { me, mark, waiting: waitingTasks(a, items ?? []) };
}
