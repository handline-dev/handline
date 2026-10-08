import { describe, expect, it } from 'vitest';
import { act } from 'react';
import { ARCH, $, mount, press, sleep, text, until } from './harness.tsx';

describe('the agent verbs menu', () => {
  async function openMenu() {
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('verbs-open'), 'the page');
    await press($('verbs-open'));
  }

  it('lists what applies to the row, without Close and Clear stall', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openMenu();
    for (const id of ['restart', 'restart_fresh', 'park', 'fold', 'move']) expect($(`verb-${id}`)).not.toBeNull();
    expect($('verb-close')).toBeNull();
    expect($('verb-clear_stall')).toBeNull();
    expect($('verb-wake')).toBeNull(); // it has an open session
    m.store.stop();
  });

  it('asks before parking, sends nothing on Cancel, shows the gerund while the daemon decides, then the daemon has parked it', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openMenu();
    await press($('verb-park'));
    expect(text()).toContain(`Park ${ARCH}?`);
    expect(text()).toContain('Its session ends; it wakes on the next task.');
    await press($('sheet-cancel'));
    expect($('sheet')).toBeNull();
    expect(m.daemon.sessions.get(ARCH)!.state).not.toBe('parked');
    m.daemon.knobs.latency(400);
    await press($('verbs-open'));
    await press($('verb-park'));
    await press($('sheet-submit'));
    await until(() => $('pending-mark')?.textContent === 'parking…', 'the pending mark', 300);
    await until(() => m.daemon.sessions.get(ARCH)!.state === 'parked', 'the daemon', 3000);
    m.store.stop();
  });

  it('sends nothing and says so when the row changed while the sheet was open', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openMenu();
    await press($('verb-fold'));
    await act(async () => void (await m.store.client.stopSession(ARCH, 'close'))); // Fold only applies to an open session
    await until(() => !m.daemon.sessions.get(ARCH)!.open, 'the session to close');
    await sleep(300); // the page reads the row again
    await press($('sheet-submit'));
    await until(() => text().includes('It changed while you looked.'), 'the notice');
    expect(m.daemon.sessions.get(ARCH)!.state).not.toBe('folded');
    await press($('sheet-submit')); // Close
    expect($('sheet')).toBeNull();
    m.store.stop();
  });

  it('puts the reason on the page when the daemon refuses, and the mark goes', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('agent.fold', 'state', 'it is mid-turn'), undefined, 'founder');
    await openMenu();
    await press($('verb-fold'));
    await press($('sheet-submit'));
    await until(() => text().includes('it is mid-turn'), 'the reason');
    await until(() => !$('pending-mark'), 'the mark to go');
    m.store.stop();
  });

  it('moves the account after choosing one, and restarts fresh', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openMenu();
    await press($('verb-move'));
    expect($('sheet-submit')?.getAttribute('aria-disabled')).toBe('true'); // no account chosen yet
    await press($('account-beta'));
    await press($('sheet-submit'));
    await until(() => m.daemon.sessions.get(ARCH)!.fx.account === 'beta', 'the daemon');
    await press($('verbs-open'));
    await press($('verb-restart_fresh'));
    expect(text()).toContain(`Restart fresh ${ARCH}?`);
    await press($('sheet-submit'));
    await until(() => !$('sheet'), 'the sheet to close');
    m.store.stop();
  });
});
