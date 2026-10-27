import { describe, expect, it } from 'vitest';
import { $, mount, press, text, until } from '#test/harness.tsx';

describe('the My turn tab', () => {
  it('counts a task given to the person, lists it with Open and Done, and drops it when done', async () => {
    const m = await mount(
      (s) => {
        s.daemon.tasks.verbs = true;
        s.daemon.tasks.seed({ id: 2999, from: 'handline', to: 'human', title: 'Pick the name', state: 'queued' });
      },
      undefined,
      'founder',
    );
    await until(() => document.body.textContent?.includes('My turn ('), 'the count on the tab');
    const tab = [...document.querySelectorAll('[role="tab"], [data-testid^="nav-"]')].find((e) => e.textContent?.startsWith('My turn'))!;
    await press(tab as HTMLElement);
    await until(() => $('turn-task-2999'), 'the task row');
    expect(text($('turn-task-2999'))).toContain('Pick the name');
    await press($('turn-task-done-2999'));
    await until(() => !$('turn-task-2999'), 'the row to leave');
    m.store.stop();
  });
});
