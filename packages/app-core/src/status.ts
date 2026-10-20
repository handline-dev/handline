// The one status label: what an agent or task is doing and why, from the daemon's `status` field (x-status). Every screen shows `text` as it is.
import type { Status } from '@handline/client';
import type { Dot } from './model.ts';

/** The rail dot for a status: waiting on a person is "waiting", on a clock is "parked", any other wait or a stop is "blocked". Words it does not know give null. */
export function dotOfStatus(s: Pick<Status, 'state' | 'reason'>): Dot | null {
  switch (s.state) {
    case 'preparing':
    case 'starting':
      return 'waking';
    case 'running':
      return 'running';
    case 'waiting':
      return s.reason === 'you' ? 'waiting' : s.reason === 'clock' ? 'parked' : 'blocked';
    case 'stopped':
      return 'blocked';
    case 'idle':
    case 'done':
      return 'idle';
    case 'off':
      return 'none';
    default:
      return null;
  }
}

/** The states whose text explains why a task is held: a running or done task needs no warning line. */
export const HELD = ['waiting', 'stopped'];
