import type { Account } from '@handline/client';
import { edit } from './cache.ts';
import { qk } from './reads.ts';
import type { WriteSpec } from './write-spec.ts';

// The daemon sends no event for a removed account, so the confirmed removal takes the row out itself (a dry run changes nothing).
export const accountRemove: WriteSpec = {
  kind: 'decided',
  label: 'remove',
  reconcile: (c, p) => {
    const { account, dry_run } = p as { account: string; dry_run?: boolean };
    if (!dry_run)
      edit<{ accounts: Account[] }>(c.qc, qk.accounts(c.d), (l) => ({ ...l, accounts: l.accounts.filter((a) => a.name !== account) }));
  },
};
