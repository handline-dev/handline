// The titles the task cards of a chat show: the newest tasks' list has most of them, and a card of an older task reads its own by id.
import { useTaskTitles } from '@handline/app-data';

/** `listed` is null until the list has come: nothing is read by id before then. At most the newest MAX are read, for a chat of years of cards. */
const MAX = 50;

export function useCardTitles(listed: Map<number, string> | null, cards: readonly number[]): Map<number, string> {
  const missing = listed
    ? [...new Set(cards)]
        .filter((t) => !listed.has(t))
        .sort((a, b) => b - a)
        .slice(0, MAX)
    : [];
  const own = useTaskTitles(missing);
  return new Map([...(listed ?? []), ...own]);
}
