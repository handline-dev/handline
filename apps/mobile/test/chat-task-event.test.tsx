import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, until } from '#test/harness.tsx';

describe('a task change in the chat', () => {
  it('is a card with the event, the task and its note, and a tap opens the task', async () => {
    const m = await mount(
      (s) =>
        s.daemon.chat.seedItem(ARCH, {
          kind: 'task_event',
          task_event: { event: 'done', task: 1684, request: null, evidence: 'auditor PASS', who: ARCH },
        }),
      undefined,
      'founder',
    );
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    const find = () => [...document.body.querySelectorAll<HTMLElement>('[data-testid^="task-event-"]')].find((e) => e.textContent?.includes('auditor PASS'));
    await until(find, 'the card');
    expect(find()!.textContent).toContain('T1684');
    expect(find()!.textContent).toContain('auditor PASS');
    await press(find()!);
    await until(() => $('task-back'), 'the task page');
    m.store.stop();
  });
});
