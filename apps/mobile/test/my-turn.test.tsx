import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { $, ARCH, mount, press, text, until } from '#test/harness.tsx';
import { HOLD_MS } from '#src/heldAnswers.ts';

describe('the Requests tab', () => {
  afterEach(() => void vi.useRealTimers());

  it('counts a task given to the person, lists it under Do by hand, and Done sends it once the hold is over', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true });
    const m = await mount(
      (s) => {
        s.daemon.tasks.verbs = true;
        s.daemon.tasks.seed({ id: 2999, from: 'handline', to: 'human', title: 'Pick the name', state: 'queued' });
      },
      undefined,
      'founder',
      { opens: 'auto' },
    );
    await until(() => $('requests-badge'), 'the count on the Requests icon');
    expect(text($('nav-requests'))).toContain('Requests');
    expect(text($('nav-requests'))).not.toContain('My turn');
    await until(() => $('request-task:2999'), 'the task row');
    expect(text($('request-group-Do by hand'))).toContain('Pick the name');
    await press($('request-task:2999'));
    await press($('request-act-done_task'));
    await act(async () => void vi.advanceTimersByTime(HOLD_MS));
    await until(() => m.daemon.tasks.get({ task: 2999 }).state === 'done', 'the daemon');
    m.store.stop();
  });
});

describe('the first screen', () => {
  it('opens on Requests when something waits, with the count as a badge on its icon', async () => {
    const m = await mount(
      (s) => s.daemon.tasks.seed({ id: 2998, from: 'handline', to: 'human', title: 'Pick a colour', state: 'queued' }),
      undefined,
      'founder',
      { opens: 'auto' },
    );
    await until(() => $('request-task:2998'), 'the Requests list');
    expect(text($('requests-badge'))).not.toBe('');
    m.store.stop();
  });

  it('opens on Agents when nothing waits', async () => {
    const m = await mount((s) => s.daemon.inbox.markRead({ ids: s.daemon.inbox.items.map((i) => i.id) }), undefined, 'founder', {
      opens: 'auto',
    });
    await until(() => $(`agent-${ARCH}`), 'the agents');
    m.store.stop();
  });
});
