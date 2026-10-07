// What account.set does to the cached account, assuming the daemon accepts it: the fields given change, the others stay.
import type { Account } from '@handline/client';

export interface AccountChange {
  label?: string;
  cap?: number;
  recover?: boolean;
  cycle?: boolean;
  cycle_end?: string | null;
}

export function accountAfter(a: Account, c: AccountChange): Account {
  const next: Account = {
    ...a,
    ...(c.label !== undefined ? { label: c.label || a.name } : {}),
    ...(c.cap !== undefined ? { cap: c.cap, cap_effective: c.cap } : {}),
    ...(c.recover !== undefined ? { recover: c.recover } : {}),
  };
  if (c.cycle === undefined && c.cycle_end === undefined) return next;
  // A date alone turns the cycle on, as the daemon does; null puts the end back to the monthly anniversary.
  const was = a.cycle;
  return {
    ...next,
    cycle: {
      on: c.cycle ?? (c.cycle_end ? true : was.on),
      end: c.cycle_end === undefined ? was.end : c.cycle_end,
      end_is_default: c.cycle_end === undefined ? was.end_is_default : c.cycle_end === null,
    },
  };
}
