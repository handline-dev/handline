import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, text, until } from './harness.tsx';
import { cacheText } from '../src/screens/AgentStrip.tsx';

async function open(prepare: Parameters<typeof mount>[0] = () => {}) {
  const m = await mount(prepare, undefined, 'founder');
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  return m;
}

describe('the strip under the agent header', () => {
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

  it('redraws when the cache runs out', async () => {
    const m = await open((s) => (s.daemon.sessions.get(ARCH)!.strip = { cache_left_s: 1 }));
    await until(() => text($('strip-cache')).includes('1s left'), 'the count');
    await until(() => text($('strip-cache')).includes('lapsed'), 'the lapse', 3000);
    m.store.stop();
  });

  it('is absent when the daemon does not serve agent.strip', async () => {
    const m = await open((s) => s.daemon.knobs.unserve('agent.strip'));
    await until(() => $('tab-chat'), 'the page');
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
