// A stalled account on the phone's Accounts tab, on the fake daemon: STALLED and its reason on the list and the page, Wake ends it, a refusal says why.
import { describe, expect, it } from 'vitest';
import type { setup } from '@handline/fake-daemon/helpers';
import { $, mount, press, text, until } from '#test/harness.tsx';

const stall = (s: ReturnType<typeof setup>) =>
  void s.daemon.accounts.stall('beta', { reason: 'login expired', error: 'authentication_failed' });
const stalled = (m: Awaited<ReturnType<typeof mount>>) => m.daemon.accounts.list().accounts.find((a) => a.name === 'beta')!.stalled;

async function open(prepare = stall, role: 'founder' | 'member' = 'founder') {
  const m = await mount(prepare, undefined, role, { role });
  await press($('nav-usage'));
  await until(() => $('account-beta'), 'the accounts');
  return m;
}

describe('a stalled account', () => {
  it('shows STALLED and its reason on the list, and Wake ends it', async () => {
    const m = await open();
    expect(text($('stall-beta'))).toContain('STALLED');
    expect(text($('stall-beta'))).toContain('login expired');
    expect($('stall-alpha')).toBeNull();
    await press($('wake-beta'));
    await until(() => $('stall-beta') === null, 'the row to lose STALLED');
    await until(() => stalled(m) === null, 'the daemon');
    m.store.stop();
  }, 20000);

  it('shows it on the account page and wakes from there', async () => {
    const m = await open();
    await press($('account-beta'));
    await until(() => $('account-detail'), 'the account');
    expect(text($('account-detail'))).toContain('STALLED');
    await press($('wake-beta'));
    await until(() => !text($('account-detail')).includes('STALLED'), 'the page to lose STALLED');
    m.store.stop();
  }, 20000);

  it('puts the stall back and says why when the daemon refuses', async () => {
    const m = await open();
    m.daemon.knobs.refuse('account.wake', 'state', 'the account is held by billing');
    await press($('wake-beta'));
    await until(() => text($('wake-error-beta')).includes('held by billing'), 'the reason', 3000);
    expect(text($('stall-beta'))).toContain('STALLED');
    m.store.stop();
  }, 20000);

  it('shows no Wake to a member phone', async () => {
    const member = await open(stall, 'member');
    expect(text($('stall-beta'))).toContain('STALLED');
    expect($('wake-beta')).toBeNull();
    member.store.stop();
  }, 20000);

  it('shows no Wake to a daemon without account.wake', async () => {
    const m = await open((s) => {
      stall(s);
      s.daemon.knobs.unserve('account.wake');
    });
    expect(text($('stall-beta'))).toContain('STALLED');
    expect($('wake-beta')).toBeNull();
    m.store.stop();
  }, 20000);
});
