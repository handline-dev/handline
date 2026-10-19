import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { ARCH, $, fill, press, until, mount } from './harness.tsx';

const openTerminal = async () => {
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $('tab-terminal'), 'the page');
  await press($('tab-terminal'));
  await until(() => $('terminal-input'), 'the input line');
};
const focus = () => act(async () => void $('terminal-input')?.focus());

describe('the phone terminal', () => {
  it('takes the input when the line is tapped, then types into the terminal', async () => {
    const m = await mount();
    await openTerminal();
    await focus();
    await until(() => $('key-esc'), 'the input is mine');
    await fill('terminal-input', 'push the branch');
    await press($('terminal-send'));
    await act(async () => void [1, 2, 3].forEach(() => m.daemon.tick()));
    await until(() => $('terminal-text')?.textContent?.includes('push the branch'), 'the typed line');
    m.store.stop();
  }, 20000);

  it('asks Take control or Fit to this device when another device holds the input', async () => {
    const m = await mount((s) => {
      s.daemon.sessions.get(ARCH)!.holder = { kind: 'device', name: 'pc', since: null };
    });
    await openTerminal();
    await focus();
    await until(() => $('terminal-ask'), 'the question');
    expect($('terminal-take')).not.toBeNull();
    expect($('terminal-fit')).not.toBeNull();
    await press($('terminal-take')); // the daemon refuses while the PC holds it, and the phone says why
    await until(() => /holds the input/.test(document.body.textContent ?? ''), 'the refusal');
    m.store.stop();
  }, 20000);
});
