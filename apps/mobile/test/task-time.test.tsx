// Where a task's time went on the phone's task page, on the fake daemon: opened with Time, segments in order adding up to the whole, no control without task.time.
import { describe, expect, it } from 'vitest';
import { $, mount, press, text, until } from '#test/harness.tsx';

const TASK = 1684;

async function open(prepare?: Parameters<typeof mount>[0]) {
  const m = await mount(prepare);
  await press($('nav-tasks'));
  await until(() => $(`task-${TASK}`), 'the task row');
  await press($(`task-${TASK}`));
  await until(() => $('task-detail'), 'the detail');
  return m;
}
const segments = () =>
  [...document.querySelectorAll<HTMLElement>('[data-testid^="time-segment-"]')].map((e) => e.dataset.testid!.replace('time-segment-', ''));

describe('task time', () => {
  it('is not read until Time is pressed, then draws the segments in order and the totals', async () => {
    const m = await open();
    expect($('task-time')).toBeNull();
    await press($('task-time-toggle'));
    await until(() => $('task-time'), 'the time');
    expect(segments()).toEqual(['queued', 'working', 'founder', 'working', 'task', 'working', 'agent', 'working', 'account', 'working']);
    expect(text($('time-segment-founder'))).toContain('needs you');
    expect(text($('time-segment-founder'))).not.toContain('waiting on you');
    expect(text($('time-segment-task'))).toContain('blocked by T1688');
    expect(text($('time-segment-account'))).toContain('at a usage limit');
    expect(text($('time-total-working'))).toContain('3 h');
    expect(text($('time-total-founder'))).toContain('30 m');
    const t = await m.client.call('task.time', { task: TASK });
    expect($('task-time')!.getAttribute('aria-label')).toBe(
      `${(Date.parse(t.segments.at(-1)!.to) - Date.parse(t.start)) / 1000} seconds in all`,
    );
    await press($('task-time-toggle'));
    expect($('task-time')).toBeNull();
    m.store.stop();
  }, 20000);

  it('shows no control on a daemon that does not serve task.time', async () => {
    const m = await open((s) => s.daemon.knobs.unserve('task.time'));
    expect($('task-history-toggle')).not.toBeNull();
    expect($('task-time-toggle')).toBeNull();
    m.store.stop();
  });
});
