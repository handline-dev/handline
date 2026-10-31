import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import { accountRemove } from '#src/account-remove.ts';
import { qk } from '#src/reads.ts';

const D = 'd1';
const run = (payload: object) => {
  const qc = new QueryClient();
  qc.setQueryData(qk.accounts(D), { accounts: [{ name: 'a' }, { name: 'b' }] });
  accountRemove.reconcile?.({ qc, d: D, me: 'me', now: '' }, payload as never, undefined as never);
  return (qc.getQueryData(qk.accounts(D)) as { accounts: { name: string }[] }).accounts.map((a) => a.name);
};

describe('removing an account', () => {
  it('a confirmed removal takes the row out of the list', () => {
    expect(run({ account: 'a' })).toEqual(['b']);
  });
  it('a dry run changes nothing', () => {
    expect(run({ account: 'a', dry_run: true })).toEqual(['a', 'b']);
  });
});
