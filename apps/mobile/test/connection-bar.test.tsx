// One bar for the connection, from its state alone: no notice, no counter, no close button.
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { $, mount, text, until } from '#test/harness.tsx';

describe('the connection bar', () => {
  it('says one thing at a time and raises no notice', async () => {
    const m = await mount();
    await until(() => $('bell'), 'the bell');
    expect(text()).not.toContain('Cannot reach');
    act(() => ((m.store.conn = 'connecting'), m.store.setUnreachable(true)));
    await until(() => text().includes('Cannot reach the daemon - retrying'), 'the bar');
    expect(text()).not.toContain('Connecting…'); // not the same fact twice
    expect(text()).not.toContain('✕');
    expect(text()).not.toContain('×');
    expect(m.store.notices.list()).toHaveLength(0);
    act(() => ((m.store.conn = 'reconnecting'), m.store.setUnreachable(false)));
    await until(() => text().includes('Reconnecting…'), 'reconnecting');
    expect(text()).not.toContain('Cannot reach');
    act(() => ((m.store.conn = 'closed'), m.store.setUnreachable(true))); // the real client's state change is what draws it; the flag flip stands in for it here
    await until(() => text().includes('Disconnected') && text().includes('Retry'), 'disconnected with Retry');
    m.store.stop();
  });
});
