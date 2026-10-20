import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { ARCH, $, until, press, mount } from './harness.tsx';
import { fitOf } from '../src/screens/TerminalPane.tsx';

const open = async (m: Awaited<ReturnType<typeof mount>>) => {
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $('tab-terminal'), 'the page');
  await press($('tab-terminal'));
  await until(() => $('terminal-text'), 'the terminal');
  return m.daemon.sessions.get(ARCH)!;
};

describe('typing in the phone terminal', () => {
  it('takes the input from the daemon on the first key and types it', async () => {
    const m = await mount(() => {}, { bytes: true });
    const s = await open(m);
    expect(s.holder.kind).toBe('daemon');
    await press($('terminal-key-tab'));
    await until(() => s.holder.name === m.store.me, 'the phone to hold the input');
    expect($('terminal-held')).toBeNull();
    m.store.stop();
  }, 20000);

  it('asks at once when another device holds control; Take control replaces it and the keys then work', async () => {
    const m = await mount((x) => {
      x.daemon.sessions.get(ARCH)!.holder = { kind: 'device', name: 'pc-app', since: new Date().toISOString() };
    }, { bytes: true });
    const s = await open(m);
    await until(() => $('terminal-held'), 'the prompt');
    expect($('terminal-held')!.textContent).toMatch(/Another device has control/);
    expect($('terminal-fit')).not.toBeNull();
    expect($('terminal-key-tab')).toBeNull(); // nothing to type with until it is taken
    await press($('terminal-take'));
    await until(() => s.holder.name === m.store.me, 'the phone to hold the input');
    await until(() => $('terminal-held') === null, 'the prompt to go');
    await press($('terminal-key-esc'));
    m.store.stop();
  }, 20000);

  it('asks when another device takes control while the terminal is open', async () => {
    const m = await mount(() => {}, { bytes: true });
    const s = await open(m);
    expect($('terminal-held')).toBeNull();
    await act(async () => {
      s.holder = { kind: 'device', name: 'pc-app', since: new Date().toISOString() };
      (m.daemon as unknown as { script: { holderChanged(s: unknown): void } }).script.holderChanged(s);
    });
    await until(() => $('terminal-held'), 'the prompt');
    m.store.stop();
  }, 20000);

  it('Fit to this device also takes control', async () => {
    const m = await mount((x) => {
      x.daemon.sessions.get(ARCH)!.holder = { kind: 'device', name: 'pc-app', since: new Date().toISOString() };
    }, { bytes: true });
    const s = await open(m);
    await until(() => $('terminal-fit'), 'the prompt');
    await press($('terminal-fit'));
    await until(() => s.holder.name === m.store.me, 'the phone to hold the input');
    m.store.stop();
  }, 20000);

  it('Start terminal cannot be pressed twice', async () => {
    const m = await mount((s) => {
      Object.assign(s.daemon.sessions.get(ARCH)!, { open: false, hasTerminal: false });
    });
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('tab-terminal'), 'the page');
    await press($('tab-terminal'));
    await until(() => $('terminal-start'), 'the button');
    await act(async () => {
      $('terminal-start')!.click();
      $('terminal-start')!.click();
    });
    expect(($('terminal-start') as HTMLButtonElement | null)?.disabled ?? true).toBe(true);
    await until(() => $('terminal-text'), 'the terminal', 8000);
    m.store.stop();
  }, 20000);
});

describe('fitOf', () => {
  it('never goes under 80 by 24 and shrinks the font to fit 80 columns on a phone', () => {
    const f = fitOf(374, 400);
    expect(f.cols).toBe(80);
    expect(f.font).toBeLessThan(11);
    expect(f.rows).toBeGreaterThanOrEqual(24);
    expect(fitOf(1200, 700).cols).toBeGreaterThan(80);
  });
});
