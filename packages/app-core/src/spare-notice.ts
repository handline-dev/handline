// The daemon's weekly-loss notice is a task for the person tagged `spare-notice`: its title is the sentence ("18% of theta's weekly use will
// be lost at 02:10. These spare tasks can use it: T6245, T6246."), and answering it "start" queues the tasks it lists. Pure.
import type { Task } from '@handline/client';
import { CLOSED } from '#src/task-table.ts';

export const SPARE_NOTICE_TAG = 'spare-notice';
/** Spare tasks shown under the sentence; the button still starts all of them. */
export const SPARE_SHOWN = 3;

export interface SpareNotice {
  task: number;
  /** The sentence without its task numbers: "... These spare tasks can use it:". */
  text: string;
  /** The spare tasks it offers, lowest number first. */
  ids: number[];
}

/** The notice a task is, or null for a task that is not one or is already closed. */
export function spareNotice(t: Pick<Task, 'id' | 'title' | 'tags' | 'state'>): SpareNotice | null {
  if (!t.tags?.includes(SPARE_NOTICE_TAG) || CLOSED.includes(t.state)) return null;
  const cut = t.title.search(/\s*T\d+/);
  const ids = [...(cut < 0 ? '' : t.title.slice(cut)).matchAll(/T(\d+)/g)].map((m) => Number(m[1]));
  return { task: t.id, text: (cut < 0 ? t.title : t.title.slice(0, cut)).trim(), ids };
}
