import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, text, until } from './harness.tsx';
import type { setup } from '@handline/fake-daemon/helpers';

/** Each take or release of the input is one recorded event of the agent. */
const record = async (m: ReturnType<typeof setup>, n: number, from = 0) => {
  await m.client.start();
  for (let i = 0; i < n; i++)
    await m.client.call((from + i) % 2 ? 'input.release' : 'input.take', { agent: ARCH, request_id: `r${from + i}` });
};
const entries = () =>
  [...document.body.querySelectorAll<HTMLElement>('[data-testid^="timeline-"]')]
    .map((e) => e.dataset.testid!)
    .filter((t) => /^timeline-\d+$/.test(t))
    .map((t) => Number(t.slice(9)));
const openTab = async () => {
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $('tab-timeline'), 'the Timeline tab');
};

describe('the Timeline tab', () => {
  it('lists newest first and Older pages until there is no more', async () => {
    const m = await mount();
    await record(m, 60);
    await openTab();
    await press($('tab-timeline'));
    await until(() => entries().length === 50, 'the first page');
    expect(entries()[0]).toBe(60);
    await press($('timeline-older'));
    await until(() => entries().length === 60, 'the older page');
    expect(entries()).toEqual(Array.from({ length: 60 }, (_, i) => 60 - i));
    expect($('timeline-older')).toBeNull();
    expect($('timeline-60')?.textContent).toMatch(/input\./);
    m.store.stop();
  }, 20000); // 60 events are 60 calls: 4.5 s alone, over 5 s under load

  it('says so when nothing is recorded', async () => {
    const m = await mount();
    await openTab();
    await press($('tab-timeline'));
    await until(() => text().includes(`Nothing is recorded for ${ARCH} yet.`), 'the empty line');
    m.store.stop();
  });

  it('the stalled mark opens it', async () => {
    const m = await mount((s) => void (s.daemon.sessions.get(ARCH)!.state = 'stalled'));
    await record(m, 3);
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('badge-stalled'), 'the mark');
    await press($('badge-stalled'));
    await until(() => entries().length === 3, 'the entries');
    expect(entries()).toEqual([3, 2, 1]);
    m.store.stop();
  });
});
