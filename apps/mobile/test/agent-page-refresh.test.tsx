import { describe, expect, it } from 'vitest';
import { ARCH, sleep, until, box, $, press, mount } from '#test/harness.tsx';

describe('the agent page', () => {
  it('never says Updating while the agent changes under an open chat', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('chat-input'), 'the chat');
    let seen = false;
    const watch = new MutationObserver(() => void (seen ||= !!box.querySelector('[data-testid="refreshing"]')));
    watch.observe(box, { childList: true, subtree: true });
    m.daemon.knobs.latency(150);
    for (let i = 0; i < 5; i++) {
      m.daemon.script.setState(m.daemon.sessions.get(ARCH)!, i % 2 ? 'running' : 'waiting');
      await sleep(200);
    }
    watch.disconnect();
    expect(seen).toBe(false);
    m.store.stop();
  });
});
