// What an agent row reads from the data layer, in one memoized object so the row's view draws again only when one of the three changes.
import { useMemo } from 'react';
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
  return useMemo(() => ({ me, mark, waiting: waitingTasks(a, items ?? []) }), [me, mark, a, items]);
}
