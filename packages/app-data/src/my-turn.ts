// What waits on the person, read once for every app: the web sidebar, its filter and the phone's tab count the same cards.
import { useEffect, useMemo, useState } from 'react';
import { laterHolds, laterKeyOf, messageRows, myTurn, navOf, type Card, type LaterAt, type MessageRow } from '@handline/app-core';
import { useMe } from '@handline/app-react';
import { useAgents, useInbox, usePrefs, useStatus, useTaskTitles, useTasks } from '#src/read.ts';

/** The cards the person's turn holds: unread items, open tasks given to them, agents that need them; the ones put off to Later apart, with when each returns; and the rows of agents with messages not seen. */
export function useMyTurn(): { cards: Card[]; later: { card: Card; at: LaterAt }[]; rows: MessageRow[]; loading: boolean } {
  const inbox = useInbox();
  const agents = useAgents();
  const tasks = useTasks({});
  const status = useStatus();
  const me = useMe();
  const items = inbox.data?.items;
  const list = agents.data?.agents;
  const rows = tasks.data?.items;
  const machine = status.data?.needs_you;
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
  const all = useMemo(
    () => myTurn({ items: items ?? [], agents: list ?? [], tasks: rows ?? [], titles, machine, me }),
    [items, list, rows, titles, machine, me],
  );
  // A time that passes returns its item on this clock; a task that ends, on the tasks topic.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  const putOff = navOf(usePrefs().data).later;
  const { cards, later } = useMemo(() => {
    const open = (id: number) => !!rows?.some((t) => t.id === id && t.state !== 'done' && t.state !== 'discarded');
    const out = { cards: [] as Card[], later: [] as { card: Card; at: LaterAt }[] };
    for (const card of all) {
      const key = laterKeyOf(card);
      const at = (key ? putOff?.[key] : null) as LaterAt | null | undefined;
      if (at && laterHolds(at, now, open)) out.later.push({ card, at });
      else out.cards.push(card);
    }
    return out;
  }, [all, putOff, rows, now]);
  const rowsOf = useMemo(() => messageRows(list ?? []), [list]);
  return { cards, later, rows: rowsOf, loading: inbox.isPending && agents.isPending };
}
