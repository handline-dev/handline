import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ARCH, $, mount, press, text, until } from '#test/harness.tsx';
import { cacheText } from '#src/screens/AgentStrip.tsx';

async function open(prepare: Parameters<typeof mount>[0] = () => {}) {
  const m = await mount(prepare, undefined, 'founder');
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $('verbs-open'), 'the page');
  await press($('verbs-open')); // the strip is in the ⋯ menu
  return m;
}

describe('the strip in the agent page menu', () => {
  it('shows model, account, context percent and the cache time left', async () => {
    const m = await open();
    await until(() => $('agent-strip'), 'the strip');
    expect(text($('strip-model'))).toContain('opus');
    expect(text($('strip-context'))).toContain('37%');
    expect(text($('strip-cache'))).toContain('4m left');
    m.store.stop();
  });

  it('draws a field the daemon cannot measure as a dash', async () => {
    const m = await open((s) => (s.daemon.sessions.get(ARCH)!.strip = { ctx_pct: null, cache_left_s: null, account: null }));
    await until(() => $('agent-strip'), 'the strip');
    for (const id of ['strip-context', 'strip-cache', 'strip-account']) expect(text($(id))).toContain('–');
    m.store.stop();
  });

  describe('with the clock driven', () => {
    // The strip counts on Date and setTimeout; the clock still moves with real time, and a test jumps it by the seconds it wants to see.
    beforeEach(() => void vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'], shouldAdvanceTime: true }));
    afterEach(() => void vi.useRealTimers());
    const jump = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
    const cache = () => text($('strip-cache'));
    const left = () => Number(/(\d+)s left/.exec(cache())?.[1]);

    it('redraws when the cache runs out', async () => {
      const m = await open((s) => (s.daemon.sessions.get(ARCH)!.strip = { cache_left_s: 60 }));
      await until(() => /\ds left/.test(cache()), 'the count');
      await jump(61_000);
      await until(() => cache().includes('lapsed'), 'the lapse');
      m.store.stop();
    });

    it('counts the seconds down while under a minute', async () => {
      const m = await open((s) => (s.daemon.sessions.get(ARCH)!.strip = { cache_left_s: 30 }));
      await until(() => /\ds left/.test(cache()), 'the count in seconds');
      const first = left();
      for (const n of [1, 2]) {
        await jump(1000);
        await until(() => left() <= first - n, `${first - n}s left`);
      }
      m.store.stop();
    });
  });

  it('is absent when the daemon does not serve agent.strip', async () => {
    const m = await open((s) => s.daemon.knobs.unserve('agent.strip'));
    await until(() => $('verbs-open'), 'the page');
    expect($('agent-strip')).toBeNull();
    m.store.stop();
  });

  it('says the cache time in the unit that changes', () => {
    expect([cacheText(240), cacheText(61), cacheText(59), cacheText(0), cacheText(-5)]).toEqual([
      '4m left',
      '2m left',
      '59s left',
      'lapsed',
      'lapsed',
    ]);
  });
});
