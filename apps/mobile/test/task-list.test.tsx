import { describe, expect, it } from 'vitest';
import { until, $, text, press, mount, fill } from '#test/harness.tsx';
import type { setup } from '@handline/fake-daemon/helpers';

const tab = (name: string) => press($(`nav-${name}`));
const MINE_DRAFT = 1700; // asked by me, not started
const verbs = (s: ReturnType<typeof setup>) => void (s.daemon.tasks.verbs = true);

describe('the task list', () => {
  it('groups the rows by status, narrows them by a search', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await tab('tasks');
    await until(() => $(`task-${MINE_DRAFT}`), 'the rows');
    expect(text()).toMatch(/Running \d/);
    expect(text()).toMatch(/Queued \d/);
    await fill('tasks-search', String(MINE_DRAFT));
    await until(() => !text().includes('Running'), 'the narrowed list');
    expect($(`task-${MINE_DRAFT}`)).not.toBeNull();
    m.store.stop();
  });

  it('marks tasks, runs a verb on them with a count, and says what happened to each', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await tab('tasks');
    await until(() => $(`task-${MINE_DRAFT}`), 'the rows');
    await press($('tasks-select'));
    expect($('tasks-bar-actions')?.getAttribute('aria-disabled')).toBe('true');
    await press($(`task-${MINE_DRAFT}`));
    await press($('tasks-bar-actions'));
    await until(() => $('batch-menu'), 'the verbs');
    await press($('batch-queue'));
    await until(() => text().includes('Queue: T1700'), 'the confirm with the task');
    await press($('sheet-submit'));
    await until(() => text().includes('all 1 changed'), 'the result');
    await until(() => m.daemon.tasks.get({ task: MINE_DRAFT }).state === 'queued', 'the daemon');
    await press($('sheet-cancel'));
    await until(() => $('tasks-bar-actions') === null, 'select mode to end');
    m.store.stop();
  }, 20000);
});
