// The task page: the line above the title, what it blocks, the state in words, Later, the one Done bar for a task that is the person's to finish, and the ... menu.
import { describe, expect, it } from 'vitest';
import { until, $, text, press, mount } from '#test/harness.tsx';
import type { setup } from '@handline/fake-daemon/helpers';

const seed = (s: ReturnType<typeof setup>) => {
  s.daemon.tasks.seed({ id: 9001, from: 'handline.architect', to: 'human', title: 'Pick the colour', state: 'doing' });
  s.daemon.tasks.seed({
    id: 9002,
    from: 'human',
    to: 'handline.pc-chat',
    title: 'Wait for the colour',
    state: 'doing',
    blockers: [{ task: 9001, reason: '' }],
  });
};
const open = async (id: number) => {
  const m = await mount(seed, undefined, 'founder');
  await press($('nav-tasks'));
  await until(() => $(`task-${id}`), 'the task row');
  await press($(`task-${id}`));
  await until(() => $('task-detail'), 'the detail');
  return m;
};

describe('task page', () => {
  it('reads who asked and who has it, says Needs you, and ends with one Done bar', async () => {
    const m = await open(9001);
    expect(text($('task-detail'))).toContain('T9001'); // local-ref-ok
    expect(text($('task-detail'))).toContain('handline.architect → you');
    expect(text($('status-menu'))).toContain('Needs you');
    await until(() => $('task-done'), 'the Done bar');
    expect($('task-blocks')?.textContent).toBe('Blocks T9002'); // local-ref-ok
    expect($('task-history-toggle')).toBeNull();
    m.store.stop();
  }, 20000);

  it('has no Done bar on a task an agent is working, and keeps History in the ... menu', async () => {
    const m = await open(9002);
    expect($('task-done')).toBeNull();
    expect($('task-blocks')).toBeNull();
    await press($('more-menu'));
    expect($('verb-history')).not.toBeNull();
    m.store.stop();
  }, 20000);
});
