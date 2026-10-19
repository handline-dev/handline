// Accept and Reject on a reported task on the phone, against the fake daemon: Accept closes it, Reject needs a note and sends the task
// back to its owner, a refusal is shown, and a task with no report waiting shows neither.
import { describe, expect, it } from 'vitest';
import type { setup } from '@handline/fake-daemon/helpers';
import { $, fill, mount, press, text, until, value } from './harness.tsx';

const REPORTED = 1800;
const DOING = 1684;
const seeded = (s: ReturnType<typeof setup>) => {
  s.daemon.tasks.verbs = true;
  s.daemon.tasks.seed({
    id: REPORTED,
    from: 'founder',
    to: 'handline.architect',
    title: 'Reported work',
    state: 'answered',
    report: { by: 'handline.architect', evidence: 'built and tested', at: new Date().toISOString() },
  });
};
const stateOf = (m: Awaited<ReturnType<typeof mount>>, id: number) => m.daemon.tasks.get({ task: id }).state;

async function openTask(id: number) {
  await press($('nav-tasks'));
  await until(() => $(`task-${id}`), 'the task row');
  await press($(`task-${id}`));
  await until(() => $('task-detail'), 'the detail');
}

describe('a reported task', () => {
  it('Accept closes it', async () => {
    const m = await mount(seeded, undefined, 'founder');
    await openTask(REPORTED);
    await press($('verb-accept'));
    await until(() => stateOf(m, REPORTED) === 'done', 'the accept');
    m.store.stop();
  }, 20000);

  it('Reject needs a note, then sends the task back to its owner', async () => {
    const m = await mount(seeded, undefined, 'founder');
    await openTask(REPORTED);
    await press($('verb-reject'));
    await press($('sheet-submit'));
    expect($('sheet-error')?.textContent).toBe('A note is required.');
    expect(stateOf(m, REPORTED)).toBe('answered');
    await fill('reject-note', 'the tests are missing');
    await press($('sheet-submit'));
    await until(() => stateOf(m, REPORTED) === 'queued', 'the reject');
    m.store.stop();
  }, 20000);

  it('keeps the note and says why when the daemon refuses', async () => {
    const m = await mount(seeded, undefined, 'founder');
    await openTask(REPORTED);
    await press($('verb-reject'));
    await fill('reject-note', 'not enough');
    m.daemon.knobs.refuse('task.reject', 'state', 'the report moved on');
    await press($('sheet-submit'));
    await until(() => text().includes('the report moved on'), 'the reason', 3000);
    expect(value('reject-note')).toBe('not enough');
    expect(stateOf(m, REPORTED)).toBe('answered');
    m.store.stop();
  }, 20000);

  it('shows Accept errors from the daemon', async () => {
    const m = await mount(seeded, undefined, 'founder');
    await openTask(REPORTED);
    m.daemon.knobs.refuse('task.accept', 'state', 'someone else decided it');
    await press($('verb-accept'));
    await until(() => $('accept-error'), 'the refusal', 3000);
    expect(text($('accept-error'))).toContain('someone else decided it');
    m.store.stop();
  }, 20000);

  it('is not offered on a task that has no report waiting', async () => {
    const m = await mount(seeded, undefined, 'founder');
    await openTask(DOING);
    expect($('verb-accept')).toBeNull();
    expect($('verb-reject')).toBeNull();
    m.store.stop();
  }, 20000);
});
