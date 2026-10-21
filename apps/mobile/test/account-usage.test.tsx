// The usage lines on the phone's Accounts tab, on the fake daemon: the fields, a dash for a missing one, and the live rows winning.
import { describe, expect, it } from 'vitest';
import { $, mount, press, text, until } from '#test/harness.tsx';

async function open() {
  const m = await mount((s) => {
    s.daemon.accounts.add({ name: 'delta', label: 'delta', plan: null, runway: '~10m to wall', assignable: false, assign_reason: '5h 97% at last reading', agents_assigned: 3, agents_running: 1 });
  });
  await press($('nav-usage'));
  await until(() => $('usage-delta'), 'the usage lines');
  return m;
}

describe('account usage lines', () => {
  it('shows plan, runway, assignable and the agents counts, a missing figure as a dash', async () => {
    const m = await open();
    const delta = text($('usage-delta'));
    expect(delta).toContain('plan — · runway ~10m to wall');
    expect(delta).toContain('cannot take another agent: 5h 97% at last reading');
    expect(delta).toContain('agents 3 assigned · 1 running · — unplaced');
    expect(text($('usage-alpha'))).toContain('plan pro');
    expect(text($('usage-alpha'))).toContain('can take another agent');
    m.store.stop();
  }, 20000);

  it('shows them on one account, and the live row still wins over the usage read', async () => {
    const m = await open();
    await press($('account-delta'));
    await until(() => $('account-detail'), 'the account');
    expect(text($('account-detail'))).toContain('runway ~10m to wall');
    m.daemon.accounts.set({ account: 'delta', label: 'renamed' });
    await until(() => text($('account-detail')).includes('renamed'), 'the live label');
    expect(text($('usage-delta'))).toContain('runway ~10m to wall');
    m.store.stop();
  }, 20000);
});
