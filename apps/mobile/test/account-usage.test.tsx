// The usage lines on the phone's Accounts tab, on the fake daemon: the fields, a dash for a missing one, and the live rows winning.
import { describe, expect, it } from 'vitest';
import { $, mount, press, text, until } from '#test/harness.tsx';

async function open() {
  const m = await mount((s) => {
    s.daemon.accounts.add({ name: 'delta', label: 'delta', plan: null, runway: '~10m to wall', assignable: false, assign_reason: '5h 97% at last reading' });
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
    expect(delta).toContain('· — unplaced');
    const alpha = (await m.store.client.call('accounts.list', { usage: true, roster: true })).accounts.find((a) => a.name === 'alpha')!;
    expect(text($('usage-alpha'))).toContain(`agents ${alpha.agents_assigned} assigned · ${alpha.agents_running} running`);
    expect(text($('usage-alpha'))).toContain('plan pro');
    expect(text($('usage-alpha'))).toContain('can take another agent');
    m.store.stop();
  }, 20000);

  it('counts the agents working now over the cap on the list row', async () => {
    const m = await open();
    const alpha = (await m.store.client.call('accounts.list', { usage: true, roster: true })).accounts.find((a) => a.name === 'alpha')!;
    await until(() => text($('account-alpha')).includes('working / cap'), 'the usage read');
    expect(text($('account-alpha'))).toContain(`${alpha.agents_running} working / cap ${alpha.cap_effective}`);
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
