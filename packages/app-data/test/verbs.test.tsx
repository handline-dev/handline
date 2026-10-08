import { act } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { setup } from '@handline/fake-daemon/helpers';
import { Store, VERBS } from '@handline/app-core';
import { useVerbMark, useVerbRun } from '../src/index.ts';
import { mount, probe, unmount, until } from './harness.tsx';

afterEach(unmount);
const ARCH = 'handline.architect';

async function connected() {
  const s = setup();
  const store = new Store(s.client);
  await store.start();
  return { ...s, store };
}

describe('the agent verb hooks, against the fake daemon', () => {
  it('runs a verb as a decided write: its gerund shows while the daemon decides, then the daemon has done it', async () => {
    const { store, daemon } = await connected();
    const v = probe(() => ({ run: useVerbRun(ARCH), mark: useVerbMark(ARCH) }));
    await mount(store, v.node);
    daemon.knobs.latency(300);
    act(() => v.get().run(VERBS.park));
    await until(() => v.get().mark === 'parking…', 'the gerund');
    await until(() => daemon.sessions.get(ARCH)!.state === 'parked', 'the daemon');
    await until(() => v.get().mark === null, 'the mark to go');
  });

  it('sends the chosen account with Move account', async () => {
    const { store, daemon } = await connected();
    const v = probe(() => ({ run: useVerbRun(ARCH) }));
    await mount(store, v.node);
    act(() => v.get().run(VERBS.move, 'beta'));
    await until(() => daemon.sessions.get(ARCH)!.fx.account === 'beta', 'the daemon');
  });

  it('puts the mark away when the daemon refuses, and leaves out the verbs the caller marks itself', async () => {
    const { store, daemon } = await connected();
    const v = probe(() => ({ run: useVerbRun(ARCH), mark: useVerbMark(ARCH), quiet: useVerbMark(ARCH, ['fold']) }));
    await mount(store, v.node);
    daemon.knobs.latency(300);
    daemon.knobs.refuse('agent.fold', 'state', 'it is mid-turn');
    act(() => v.get().run(VERBS.fold));
    await until(() => v.get().mark === 'folding…', 'the gerund');
    expect(v.get().quiet).toBeNull();
    await until(() => v.get().mark === null, 'the mark to go');
    expect(store.error).toContain('it is mid-turn');
  });
});
