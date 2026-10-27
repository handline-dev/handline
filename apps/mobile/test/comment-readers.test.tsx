// The read button under a founder's comment: how many read it, each reader's state in a sheet, Send now for a held one, gone when all have read.
import { describe, expect, it } from 'vitest';
import { until, $, text, press, mount } from '#test/harness.tsx';

describe('who has read a comment', () => {
  it('counts readers, lists each with its state, sends a held one now', async () => {
    let memo = 0;
    const m = await mount(
      (s) => {
        s.daemon.tasks.verbs = true;
        memo = s.daemon.tasks.memo({ task: 1684, text: 'Please use 16 px' }, 'add', 'human');
        s.daemon.tasks.setReaders(1684, memo, [
          { agent: 'handline.pc', request: 1, state: 'read', at: new Date().toISOString() },
          { agent: 'handline.ux', request: 2, state: 'held', at: new Date().toISOString(), minutes: 4 },
          { agent: 'handline.mobile', request: 3, state: 'sent', at: new Date().toISOString() },
        ]);
      },
      undefined,
      'founder',
    );
    await press($('nav-tasks'));
    await until(() => $('task-1684'), 'the task row');
    await press($('task-1684'));
    await until(() => $(`memo-readers-${memo}`), 'the read button');
    expect(text()).toContain('1/3 read');
    await press($(`memo-readers-${memo}`));
    await until(() => $('readers-sheet'), 'the sheet');
    expect($('reader-handline.pc')?.textContent).toContain('ago');
    expect($('reader-handline.ux')?.textContent).toContain('waits for the next pickup, in about 4 min');
    expect($('reader-handline.mobile')?.textContent).toContain('sent, not read');
    expect($('reader-send-handline.pc')).toBeNull();
    await press($('reader-send-handline.ux'));
    await until(() => $('reader-send-handline.ux') === null, 'the held reader to be sent');
    m.store.stop();
  });
});
