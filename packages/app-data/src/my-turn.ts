// What waits on the person, read once for every app: the web sidebar, its filter and the phone's tab count the same cards.
import { useMemo } from 'react';
import { messageRows, myTurn, type Card, type MessageRow } from '@handline/app-core';
import { useMe } from '@handline/app-react';
import { useAgents, useInbox, useTaskTitles, useTasks } from '#src/read.ts';

/** The cards the person's turn holds: unread items, open tasks given to them, agents that need them; and the rows of agents with messages not seen. */
export function useMyTurn(): { cards: Card[]; rows: MessageRow[]; loading: boolean } {
  const inbox = useInbox();
  const agents = useAgents();
  const tasks = useTasks({});
  const me = useMe();
  const items = inbox.data?.items;
  const list = agents.data?.agents;
  const rows = tasks.data?.items;
  // An ask on a task older than the newest page still gets the task's title, read by its id.
  const known = useMemo(() => new Set((rows ?? []).map((t) => t.id)), [rows]);
  const missing = useMemo(
    () => [
      ...new Set(
        (items ?? []).flatMap((i) =>
          !i.read && i.kind === 'ask' && i.memo != null && i.task != null && !known.has(i.task) ? [i.task] : [],
        ),
      ),
    ],
    [items, known],
  );
  const titles = useTaskTitles(missing);
  const cards = useMemo(
    () => myTurn({ items: items ?? [], agents: list ?? [], tasks: rows ?? [], titles, me }),
    [items, list, rows, titles, me],
  );
  const rowsOf = useMemo(() => messageRows(list ?? []), [list]);
  return { cards, rows: rowsOf, loading: inbox.isPending && agents.isPending };
}
