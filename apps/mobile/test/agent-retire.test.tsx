// Delete agent on the phone's agent page, on the fake daemon: delete and leave, a refusal, a member phone, an unserved method.
import { describe, expect, it } from 'vitest';
import type { setup } from '@handline/fake-daemon/helpers';
import { $, ARCH, mount, press, text, until } from '#test/harness.tsx';

const LEAF = 'handline.term-build'; // parked, nothing under it
const none = () => {};

async function openMenu(m: Awaited<ReturnType<typeof mount>>, agent: string) {
  await until(() => $(`agent-${agent}`), 'the rows');
  await press($(`agent-${agent}`));
  await until(() => $('verbs-open'), 'the page');
  await press($('verbs-open'));
  return m;
}

describe('Delete agent', () => {
  it('asks first, deletes a leaf agent and leaves the page', async () => {
    const m = await openMenu(await mount(none, undefined, 'founder', { role: 'founder' }), LEAF);
    await press($('verb-delete'));
    expect(text($('sheet'))).toContain(`Delete ${LEAF}?`);
    expect(text($('sheet'))).toContain('folder is moved to the archive');
    expect(m.daemon.sessions.has(LEAF)).toBe(true);
    await press($('sheet-submit'));
    await until(() => !m.daemon.sessions.has(LEAF), 'the retire');
    await until(() => $('verbs-open') === null && $(`agent-${ARCH}`), 'the list');
    expect($(`agent-${LEAF}`)).toBeNull();
    m.store.stop();
  }, 20000);

  it('keeps the sheet and says why when the daemon refuses', async () => {
    const m = await openMenu(await mount(none, undefined, 'founder', { role: 'founder' }), ARCH);
    await press($('verb-delete'));
    await press($('sheet-submit'));
    await until(() => text($('sheet-error')).includes('has a session running'), 'the reason', 3000);
    expect($('sheet')).not.toBeNull();
    expect(m.daemon.sessions.has(ARCH)).toBe(true);
    await press($('sheet-cancel'));
    expect($('sheet')).toBeNull();
    m.store.stop();
  }, 20000);

  it('shows no row to a member phone', async () => {
    const m = await openMenu(await mount(none, undefined, 'member', { role: 'member' }), LEAF);
    expect($('verb-settings')).not.toBeNull();
    expect($('verb-delete')).toBeNull();
    m.store.stop();
  }, 20000);

  it('shows no row on a daemon without agent.retire', async () => {
    const m = await openMenu(
      await mount((s: ReturnType<typeof setup>) => s.daemon.knobs.unserve('agent.retire'), undefined, 'founder', { role: 'founder' }),
      LEAF,
    );
    expect($('verb-delete')).toBeNull();
    m.store.stop();
  }, 20000);
});
