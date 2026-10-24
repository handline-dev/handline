// The titles the task cards of a chat show: the newest tasks' list has most of them, and a card of an older task reads its own by id.
import type { TranscriptItem } from '@handline/client';
import type { TaskFold, TimelineEntry } from '@handline/app-core';
import { useServes, useTaskTitles, useTasks } from '@handline/app-data';

/** At most the newest MAX missing titles are read by id, for a chat of years of cards. */
const MAX = 50;

export function useCardTitles(items: readonly TranscriptItem[], entries: readonly (TimelineEntry | TaskFold)[]): Map<number, string> {
  const { served } = useServes('tasks.list');
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
  const own = useTaskTitles(missing);
  return new Map([...(listed ?? []), ...own]);
}
