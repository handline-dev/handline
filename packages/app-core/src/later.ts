// Later: the person puts an item of Requests off until a time or until a task is done (NavPrefs.later, by `kind:id` of the For you item). Pure.
import type { Card } from '#src/my-turn.ts';
import { taskRef } from '#src/tasks.ts';

/** Why an item is out of its group and when it returns: at `until` (ISO time), or when the task `after_task` ends. */
export type LaterAt = { until: string } | { after_task: number };

/** The For you key of a card, or null for one that cannot be put off (a session's prompt, an HR proposal: an agent is stopped on them). */
export function laterKeyOf(c: Pick<Card, 'kind' | 'task' | 'memo'>): string | null {
  if (c.kind === 'approval' || c.kind === 'dialog') return null;
  if (c.kind === 'machine') return 'machine:disk';
  if (c.memo != null) return `comment:${c.memo}`;
  return c.task != null ? `task:${c.task}` : null;
}

/** Whether the deferral still holds at `now`: its time is ahead, or its task is still open (`open` says which tasks are). */
export const laterHolds = (l: LaterAt | null | undefined, now: number, open: (task: number) => boolean): boolean =>
  !!l && ('until' in l ? Date.parse(l.until) > now : open(l.after_task));

const at = (d: Date, hour: number): Date => (d.setHours(hour, 0, 0, 0), d);

/** The quick choices of the Later menu from `now`: this afternoon 15:00 (only before it), tomorrow 09:00, next Monday 09:00. */
export function laterChoices(now: number): { label: string; until: string }[] {
  const d = new Date(now);
  const afternoon = at(new Date(now), 15);
  const tomorrow = at(new Date(now), 9);
  tomorrow.setDate(d.getDate() + 1);
  const monday = at(new Date(now), 9);
  monday.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7));
  const iso = (x: Date) => x.toISOString();
  return [
    ...(afternoon.getTime() > now ? [{ label: 'This afternoon 15:00', until: iso(afternoon) }] : []),
    { label: 'Tomorrow 09:00', until: iso(tomorrow) },
    { label: 'Next Mon 09:00', until: iso(monday) },
  ];
}

/** When the item returns, in words: "Fri 09:00" or "when T9 is done". */
export const laterWords = (l: LaterAt): string =>
  'until' in l
    ? new Date(l.until).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    : `when ${taskRef(l.after_task)} is done`;
