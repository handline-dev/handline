// The paired devices in the phone's settings, on the fake daemon: the rows, Revoke behind a confirm and the row gone on the refetch, and no section for a member or a daemon without devices.list.
import { describe, expect, it } from 'vitest';
import { $, box, mount, press, until } from '#test/harness.tsx';

const text = (el: Element | null = box) => el?.textContent ?? '';
async function settings(opts: Parameters<typeof mount> = []) {
  const m = await mount(...opts);
  await press($('nav-agents'));
  await press($('agents-menu'));
  await press($('settings-open'));
  return m;
}

describe('paired devices', () => {
  it('lists each device with when it was seen', async () => {
    const m = await settings();
    await until(() => $('device-d1'), 'the devices');
    expect(text($('device-d1'))).toContain('Handline desktop');
    expect(text($('device-d1'))).toContain('connected');
    expect(text($('device-d2'))).toContain('phone-3b54');
    expect(text($('device-d2'))).toMatch(/seen .* ago/);
    m.store.stop();
  });

  it('asks before revoking, and the row goes on the refetch', async () => {
    const m = await settings();
    await until(() => $('device-d2'), 'the devices');
    await press($('revoke-d2'));
    expect(text($('revoke-sheet'))).toContain('Revoke phone-3b54?');
    await press($('revoke-cancel'));
    expect($('revoke-sheet')).toBeNull();
    expect($('device-d2')).not.toBeNull();
    await press($('revoke-d2'));
    await press($('revoke-go'));
    await until(() => !$('device-d2'), 'the row to go');
    expect($('device-d1')).not.toBeNull();
    m.store.stop();
  });

  it('shows no section to a member', async () => {
    const m = await settings([undefined, undefined, undefined, { role: 'member' }]);
    await until(() => $('unpair'), 'the page');
    expect($('paired-devices')).toBeNull();
    m.store.stop();
  });

  it('shows no section on a daemon that does not serve devices.list', async () => {
    const m = await settings([(s) => s.daemon.knobs.unserve('devices.list')]);
    await until(() => $('unpair'), 'the page');
    expect($('paired-devices')).toBeNull();
    m.store.stop();
  });
});
