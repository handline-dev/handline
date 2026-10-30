import { describe, expect, it } from 'vitest';
import { $, ARCH, mount, press, text, until } from '#test/harness.tsx';

describe('the Requests tab', () => {
  it('counts a task given to the person, lists it with Open and Done, and drops it when done', async () => {
    const m = await mount(
      (s) => {
        s.daemon.tasks.verbs = true;
        s.daemon.tasks.seed({ id: 2999, from: 'handline', to: 'human', title: 'Pick the name', state: 'queued' });
      },
      undefined,
      'founder',
    );
    await until(() => $('requests-badge'), 'the count on the Requests icon');
    await press($('nav-requests'));
    expect(text($('nav-requests'))).toContain('Requests');
    expect(text($('nav-requests'))).not.toContain('My turn');
    await until(() => $('turn-task-2999'), 'the task row');
    await until(() => $('turn-task-2999'), 'the task row');
    expect(text($('turn-task-2999'))).toContain('Pick the name');
    await press($('turn-task-done-2999'));
    await until(() => !$('turn-task-2999'), 'the row to leave');
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
    await until(() => $('turn-task-2998'), 'the Requests list');
    expect(text($('requests-badge'))).not.toBe('');
    expect(text($('requests-badge'))).toBe(text($('unread-count')));
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
