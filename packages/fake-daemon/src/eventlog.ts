// The fake daemon's event log: the retained events with their cursors, and what a subscriber resuming from a cursor is owed.
import type { Event as WireEvent } from '@handline/contract';

export class EventLog {
  /** The cursor of the newest event. */
  head = 0;
  /** The cursor of the newest event dropped by retention. */
  private floor = 0;
  private events: WireEvent[] = [];
  private retention: number;

  constructor(retention: number) {
    this.retention = retention;
  }

  /** Gives the event its cursor and keeps it, dropping the oldest past the retention. */
  append(e: Omit<WireEvent, 'cursor'>): WireEvent {
    const kept = { cursor: ++this.head, ...e };
    this.events.push(kept);
    while (this.events.length > this.retention) this.floor = this.events.shift()!.cursor;
    return kept;
  }

  /** The events after `since`, or null when retention dropped some of them (the subscriber is told of a gap). */
  after(since: number): WireEvent[] | null {
    return since < this.floor ? null : this.events.filter((e) => e.cursor > since);
  }
}
