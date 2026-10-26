// The titles the task cards of a chat show: the newest tasks' list has most of them, and a card of an older task reads its own by id.
import type { TranscriptItem } from '@handline/client';
import type { TaskFold, TimelineEntry } from '@handline/app-core';
import { useServes, useTaskTitleReads, useTasks } from '@handline/app-data';

/** At most the newest MAX missing titles are read by id, for a chat of years of cards. */
const MAX = 50;

/** `settled` says the cards' titles are known: the list and the reads of the cards it lacks have come, or there are no cards, or the daemon will not list them. */
export function useCardTitles(
  items: readonly TranscriptItem[],
  entries: readonly (TimelineEntry | TaskFold)[],
): { titles: Map<number, string>; settled: boolean } {
  const { served, open } = useServes('tasks.list');
  const tasks = useTasks({ all: true, limit: 200 }); // done tasks too: a card of a closed task keeps its title
  const listed = served && tasks.data ? new Map(tasks.data.items.map((t) => [t.id, t.title])) : null;
  const cards = [
    ...items.flatMap((i) => (i.task_event ? [i.task_event.task] : [])),
    ...entries.flatMap((e) => (e.kind === 'note' ? [e.task] : [])),
  ];
  // Nothing is read by id before the list has come.
  const missing = listed
    ? [...new Set(cards)]
        .filter((t) => !listed.has(t))
        .sort((a, b) => b - a)
        .slice(0, MAX)
    : [];
  const { titles: own, pending } = useTaskTitleReads(missing);
  const settled = !cards.length || !served || !open || ((tasks.isSuccess || tasks.isError) && !pending);
  return { titles: new Map([...(listed ?? []), ...own]), settled };
}
