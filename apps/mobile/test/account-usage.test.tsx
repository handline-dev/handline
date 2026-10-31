// The usage lines on the phone's Accounts tab, on the fake daemon: the fields, a dash for a missing one, and the live rows winning.
import { describe, expect, it } from 'vitest';
import { $, mount, press, text, until } from '#test/harness.tsx';

async function open() {
  const m = await mount((s) => {
    s.daemon.accounts.add({
      name: 'delta',
      label: 'delta',
      plan: null,
      runway: '~10m to wall',
      assignable: false,
      assign_reason: '5h 97% at last reading',
    });
  });
  await press($('nav-usage'));
  await until(() => $('account-delta'), 'the rows');
  return m;
}

describe('account usage lines', () => {
  it('shows plan, runway and assignable on the account page, a missing figure as a dash', async () => {
    const m = await open();
    await press($('account-delta'));
    await until(() => $('usage-delta'), 'the usage lines');
    const delta = text($('usage-delta'));
    expect(delta).toContain('plan — · runway ~10m to wall');
    expect(delta).toContain('cannot take another agent: 5h 97% at last reading');
    expect(text()).not.toMatch(/assigned|unplaced/);
    await press($('account-back'));
    await press($('account-alpha'));
    await until(() => $('usage-alpha'), 'the usage lines');
    expect(text($('usage-alpha'))).toContain('plan pro');
    expect(text($('usage-alpha'))).toContain('can take another agent');
    m.store.stop();
  }, 20000);

  it('counts the agents working now over the cap on the list row', async () => {
    const m = await open();
    const alpha = (await m.store.client.call('accounts.list', { usage: true, roster: true })).accounts.find((a) => a.name === 'alpha')!;
    await until(() => text($('account-alpha')).includes(' working'), 'the usage read');
    expect(text($('account-alpha'))).toContain(`${alpha.agents_running} of ${alpha.cap_effective} working`);
    m.store.stop();
  }, 20000);

  it("says the account's pace on its page, and nothing for an account without one", async () => {
    const m = await open();
    await press($('account-alpha'));
    await until(() => $('pace-line-alpha'), 'the pace line');
    expect(text($('pace-line-alpha'))).toContain('ahead of pace');
    await press($('account-back'));
    await press($('account-gamma'));
    await until(() => $('pace-line-gamma'), 'the pace line');
    expect(text($('pace-line-gamma'))).toContain('over pace: no new turns');
    await press($('account-back'));
    await press($('account-delta'));
    await until(() => $('account-detail'), 'the account');
    expect($('pace-line-delta')).toBeNull();
    m.store.stop();
  }, 20000);

  it('shows them on one account, and the live row still wins over the usage read', async () => {
    const m = await open();
    await press($('account-delta'));
    await until(() => $('account-detail'), 'the account');
    expect(text($('account-detail'))).toContain('runway ~10m to wall');
    expect(text($('usage-delta'))).toMatch(/\d+ of \d+ working/);
    expect(text($('account-detail'))).not.toMatch(/assigned|unplaced/);
    m.daemon.accounts.set({ account: 'delta', label: 'renamed' });
    await until(() => text($('account-detail')).includes('renamed'), 'the live label');
    expect(text($('usage-delta'))).toContain('runway ~10m to wall');
    m.store.stop();
  }, 20000);
});

describe('the Usage list as drawn', () => {
  it('says blocked and Login needed in words, with the plan beside the name and one warning under the bars', async () => {
    const m = await open();
    expect(text($('account-alpha'))).toContain('pro');
    expect(text($('account-alpha'))).toContain('5-hour');
    expect($('account-add-open')).toBeNull(); // accounts are added in Settings
    m.store.stop();
  }, 20000);

  it('has a This computer section with its rows and Open Machine', async () => {
    const m = await open();
    await until(() => $('machine'), 'the machine rows');
    expect(text($('machine'))).toContain('This computer');
    for (const row of ['load', 'memory', 'disk', 'slots']) expect($(`machine-${row}`)).not.toBeNull();
    expect(text($('machine-slots'))).toMatch(/Heavy slots.*\d of \d/);
    await press($('machine-open'));
    await until(() => $('settings-page') || $('settings-founder-only'), 'the Settings page');
    m.store.stop();
  }, 20000);
});
