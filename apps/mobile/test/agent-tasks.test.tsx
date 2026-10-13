import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, text, until } from './harness.tsx';

const past = '2026-01-01T00:00:00Z';
const clock = { length_s: 60, default: false, start: past, end: past, overdue_since: past, told: 1, left_s: null };

async function openTasks(prepare: Parameters<typeof mount>[0] = () => {}) {
  const m = await mount(prepare, undefined, 'founder');
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $('tab-tasks'), 'the tab');
  await press($('tab-tasks'));
  return m;
}

describe('the Tasks tab of the agent page', () => {
  it('lists what the agent holds and opened, overdue first, and a closed one dimmed and worded', async () => {
    const m = await openTasks((s) =>
      s.daemon.tasks.seed({ id: 1999, from: 'founder', to: ARCH, title: 'Late one', state: 'doing', clock }),
    );
    await until(() => $('agent-task-1999'), 'the rows');
    const ids = [...document.querySelectorAll('[data-testid^="agent-task-"]')].map((e) => e.getAttribute('data-testid'));
    expect(ids[0]).toBe('agent-task-1999'); // overdue before the open ones
    expect(ids).toContain('agent-task-1684'); // held
    expect(ids).toContain('agent-task-1688'); // opened (held by term-build)
    expect(ids.at(-1)).toBe('agent-task-1650'); // closed last
    expect(text($('agent-task-1999'))).toContain('overdue');
    expect(text($('agent-task-1688'))).toContain('held by handline.term-build');
    expect(text($('agent-task-1650'))).toContain('done (closed)');
    expect($('agent-task-1650')!.style.opacity).toBe('0.6');
    m.store.stop();
  });

  it('says so when the list is capped', async () => {
    const m = await openTasks((s) => {
      for (let id = 5000; id < 5201; id++) s.daemon.tasks.seed({ id, from: 'founder', to: ARCH, title: `t${id}`, state: 'doing' });
    });
    await until(() => $('tasks-capped'), 'the note');
    expect(text($('tasks-capped'))).toContain('older tasks are not listed');
    m.store.stop();
  });

  it('opens the task page and comes back', async () => {
    const m = await openTasks();
    await until(() => $('agent-task-1684'), 'the rows');
    await press($('agent-task-1684'));
    await until(() => $('task-detail'), 'the task page');
    await press($('task-back'));
    await until(() => $('agent-task-1684'), 'the list');
    m.store.stop();
  });

  it('has no tab when the daemon does not serve tasks.list', async () => {
    const m = await mount((s) => s.daemon.knobs.unserve('tasks.list'), undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('tab-chat'), 'the page');
    expect($('tab-tasks')).toBeNull();
    m.store.stop();
  });
});
