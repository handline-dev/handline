// An agent's recorded events, newest first, for agent.timeline.
import type { TimelineEntry } from '@handline/contract';
import { invalidParams } from './errors.ts';

export class Timeline {
  private id = 0;
  private entries = new Map<string, TimelineEntry[]>();

  /** Records one event of an agent and returns it. */
  record(agent: string, f: Omit<TimelineEntry, 'id'>): TimelineEntry {
    const entry = { id: ++this.id, ...f };
    this.entries.set(agent, [...(this.entries.get(agent) ?? []), entry]);
    return entry;
  }

  /** The params are checked before the agent is looked up, as the daemon does. */
  static check(p: Record<string, unknown>): void {
    for (const [field, least, most] of [
      ['limit', 1, 200],
      ['before', 0, Infinity],
    ] as const) {
      const v = p[field];
      if (v !== undefined && !(Number.isInteger(v) && (v as number) >= least && (v as number) <= most)) {
        throw invalidParams(`${field} must be ${field === 'limit' ? 'a whole number from 1 to 200' : 'an event id'}`, field);
      }
    }
  }

  page(agent: string, p: Record<string, unknown>): { items: TimelineEntry[]; more: boolean } {
    const limit = typeof p.limit === 'number' ? p.limit : 50;
    const older = (this.entries.get(agent) ?? []).filter((e) => typeof p.before !== 'number' || e.id < p.before).reverse();
    return { items: older.slice(0, limit), more: older.length > limit };
  }
}
