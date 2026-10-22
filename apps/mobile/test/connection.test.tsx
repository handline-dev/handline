// The phone's Connection group in Settings on the fake daemon: the rows of connection.get, a missing listener, and who sees it.
import { describe, expect, it } from 'vitest';
import { $, box, mount, press, until } from '#test/harness.tsx';

const text = (el: Element | null = box) => el?.textContent ?? '';
async function open(prepare?: Parameters<typeof mount>[0], role?: 'member') {
  const m = await mount(prepare, { admin: true }, undefined, { role });
  await press($('nav-agents'));
  await press($('agents-menu'));
  await press($('settings-open'));
  return m;
}

describe('the phone Connection group', () => {
  it('shows the rows equal to the fake’s answer', async () => {
    const m = await open();
    await until(() => $('conn-address'), 'the connection');
    const info = await m.store.client.call('connection.get', {});
    expect(text($('conn-address'))).toContain(info.listener!.address);
    expect(text($('conn-encryption'))).toContain('Encrypted');
    expect(text($('conn-fingerprint'))).toContain(info.listener!.fingerprint!);
    expect($('conn-tailscale')).toBeNull();
    expect(text($('conn-slots'))).toContain(String(info.heavy_slots.slots));
    expect(text($('conn-dev'))).toContain('Off');
    m.store.stop();
  });

  it('says there is no network listener when the answer has none', async () => {
    const m = await open((s) => (s.daemon.admin.noListener = true));
    await until(() => $('conn-no-listener'), 'the note');
    expect($('conn-address')).toBeNull();
    expect(text($('conn-dev'))).toContain('Off');
    m.store.stop();
  });

  it('shows no group to a member phone or on a daemon without connection.get', async () => {
    const member = await open(undefined, 'member');
    await until(() => text().includes('Only the founder'), 'the page');
    expect($('connection')).toBeNull();
    member.store.stop();
    const m = await open((s) => s.daemon.knobs.unserve('connection.get'));
    await until(() => $('unpair'), 'the page');
    expect($('connection')).toBeNull();
    m.store.stop();
  });
});
