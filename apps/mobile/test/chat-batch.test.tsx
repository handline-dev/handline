import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, until } from '#test/harness.tsx';

describe('the batch-close card in the chat', () => {
  it('is one card with a line per task, and a line opens its task', async () => {
    const m = await mount(
      (s) =>
        s.daemon.chat.seedItem(ARCH, {
          kind: 'task_batch',
          task_batch: {
            batch: 'b1',
            who: 'handline',
            tasks: [
              { task: 11, title: 'Stale one', reason: 'covered by T12' },
              { task: 12, title: 'Stale two', reason: 'moot' },
            ],
          },
        }),
      undefined,
      'founder',
    );
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    const find = () => document.body.querySelector<HTMLElement>('[data-testid^="batch-"]:not([data-testid^="batch-task-"])');
    await until(find, 'the card');
    const card = find()!;
    expect(card.textContent).toContain('discarded 2 tasks');
    expect(card.textContent).toContain('T11 Stale one');
    expect(card.textContent).toContain('covered by T12');
    await press($('batch-task-12'));
    await until(() => $('task-back'), 'the task page');
    await press($('task-back'));
    await until(() => $('chat-input'), 'the chat again');
    m.store.stop();
  });
});
