import { afterEach, describe, expect, it, vi } from 'vitest';
import { setup } from '@handline/fake-daemon/helpers';
import { Store } from '@handline/app-core';
import { useTaskActions } from '#src/index.ts';
import { mount, probe, unmount } from '#test/harness.tsx';

afterEach(unmount);

describe('the task verb table', () => {
  it('sends each row verb with the arguments the daemon takes', async () => {
    const s = setup();
    const store = new Store(s.client);
    await store.start();
    const call = vi.spyOn(s.client, 'call');
    const v = probe(() => useTaskActions());
    await mount(store, v.node);
    const { verbs } = v.get();
    const sent = async (run: Promise<unknown>) => {
      await run.catch(() => {});
      return call.mock.calls.at(-1);
    };
    expect(await sent(verbs.done(7, 'shipped'))).toEqual(['task.done', { task: 7, evidence: 'shipped', reason: true }]);
    expect(await sent(verbs.reopen(7, ''))).toEqual(['task.reopen', { task: 7 }]);
    expect(await sent(verbs.reopen(7, 'again'))).toEqual(['task.reopen', { task: 7, note: 'again' }]);
    expect(await sent(verbs.queue(7, ''))).toEqual(['task.queue', { tasks: [7] }]);
    expect(await sent(verbs.cancel(7, ''))).toEqual(['task.cancel', { task: 7 }]);
    expect(await sent(verbs.priority(7, '3'))).toEqual(['task.edit', { task: 7, priority: 3 }]);
    expect(await sent(verbs.timebox(7, '2h'))).toEqual(['task.edit', { task: 7, time_box: '2h' }]);
  });
});
