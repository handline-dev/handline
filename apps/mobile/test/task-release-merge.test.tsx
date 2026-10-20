// Release and Merge on the phone's task page, on the fake daemon: a reason is required to release, the task leaves doing, a merge needs at
// least one other task chosen and folds them, and a refusal keeps the sheet with its reason.
import { describe, expect, it } from 'vitest';
import type { setup } from '@handline/fake-daemon/helpers';
import { $, fill, mount, press, text, until, value , verb } from '#test/harness.tsx';

const DOING = 1684;
const OTHER = 1690; // queued
const verbs = (s: ReturnType<typeof setup>) => void (s.daemon.tasks.verbs = true);
const stateOf = (m: Awaited<ReturnType<typeof mount>>, id: number) => m.daemon.tasks.get({ task: id }).state;

async function openTask(id: number) {
  await press($('nav-tasks'));
  await until(() => $(`task-${id}`), 'the task row');
  await press($(`task-${id}`));
  await until(() => $('task-detail'), 'the detail');
}

describe('Release a task', () => {
  it('sends nothing without a reason, then releases and the task leaves doing', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(DOING);
    await press(await verb('release'));
    await press($('sheet-submit'));
    expect($('sheet-error')?.textContent).toBe('A reason is required.');
    expect(stateOf(m, DOING)).toBe('doing');
    await fill('release-reason', 'stuck on the review');
    await press($('sheet-submit'));
    await until(() => $('sheet') === null, 'the sheet to close');
    await until(() => stateOf(m, DOING) === 'queued', 'the release');
    m.store.stop();
  }, 20000);

  it('keeps the reason and says why when the daemon refuses', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(DOING);
    await press(await verb('release'));
    await fill('release-reason', 'moving on');
    m.daemon.knobs.refuse('task.release', 'state', 'the agent is mid-turn');
    await press($('sheet-submit'));
    await until(() => text().includes('the agent is mid-turn'), 'the reason', 3000);
    expect(value('release-reason')).toBe('moving on');
    m.store.stop();
  }, 20000);

  it('offers Release only on a doing task', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(OTHER);
    expect($('verb-release')).toBeNull();
    m.store.stop();
  }, 20000);
});

describe('Merge tasks', () => {
  it('needs one other task chosen, then folds it into this one', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(DOING);
    await press(await verb('merge'));
    await press($('sheet-submit'));
    expect($('sheet-error')?.textContent).toBe('Pick at least one task to fold in.');
    expect($(`merge-${DOING}`)).toBeNull();
    await until(() => $(`merge-${OTHER}`), 'the other task');
    await press($(`merge-${OTHER}`));
    await press($('sheet-submit'));
    await until(() => stateOf(m, OTHER) === 'discarded', 'the fold');
    expect(stateOf(m, DOING)).toBe('doing');
    m.store.stop();
  }, 20000);

  it('says why on a refusal and keeps the choice', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(DOING);
    await press(await verb('merge'));
    await until(() => $(`merge-${OTHER}`), 'the other task');
    await press($(`merge-${OTHER}`));
    m.daemon.knobs.refuse('task.merge', 'state', 'that task is held');
    await press($('sheet-submit'));
    await until(() => text().includes('that task is held'), 'the reason', 3000);
    expect(stateOf(m, OTHER)).toBe('queued');
    expect($(`merge-${OTHER}`)).toBeTruthy();
    m.store.stop();
  }, 20000);

  it('disables the control, with the reason, on a daemon without the verbs', async () => {
    const m = await mount(undefined, undefined, 'founder');
    await openTask(DOING);
    await verb('merge');
    expect(($('verb-merge') as HTMLButtonElement).disabled).toBe(true);
    m.store.stop();
  }, 20000);
});
