// The one-tap buttons on a chat message that waits on a decision (ChatMessage.actions), against the fake daemon: Accept calls the verb the
// action names, Send back asks for a note first, and a refusal is shown on the message.
import { describe, expect, it } from 'vitest';
import type { setup } from '@handline/fake-daemon/helpers';
import { ARCH, $, fill, mount, press, text, until } from './harness.tsx';

const TASK = 1800;
const seeded = (s: ReturnType<typeof setup>) => {
  s.daemon.tasks.verbs = true;
  s.daemon.tasks.seed({
    id: TASK,
    from: 'founder',
    to: ARCH,
    title: 'Reported work',
    state: 'review',
    report: { by: ARCH, evidence: 'built and tested', at: new Date().toISOString() },
  });
  const id = s.daemon.chat.seed(ARCH, {
    body: 'T1800 is ready for your decision.',
    from: ARCH,
    to: 'founder',
    actions: [
      { key: 'accept', label: 'Accept', verb: 'task.accept', task: TASK, params: {} },
      { key: 'send_back', label: 'Send back', verb: 'task.reject', task: TASK, params: {}, needs: 'note' },
    ],
  });
  return id;
};
const stateOf = (m: Awaited<ReturnType<typeof mount>>) => m.daemon.tasks.get({ task: TASK }).state;
let msg = 0;
async function openChat(prepare = seeded) {
  const m = await mount((s) => void (msg = prepare(s)), undefined, 'founder');
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $(`action-${msg}-accept`), 'the buttons');
  return m;
}

describe('one-tap actions on a chat message', () => {
  it('Accept closes the task', async () => {
    const m = await openChat();
    await press($(`action-${msg}-accept`));
    await until(() => stateOf(m) === 'done', 'the accept');
    m.store.stop();
  }, 20000);

  it('Send back asks for a note, then sends the task back', async () => {
    const m = await openChat();
    await press($(`action-${msg}-send_back`));
    await fill(`action-note-${msg}`, 'the tests are missing');
    await press($('sheet-submit'));
    await until(() => stateOf(m) === 'queued', 'the send back');
    m.store.stop();
  }, 20000);

  it('shows why when the daemon refuses', async () => {
    const m = await openChat();
    m.daemon.knobs.refuse('task.accept', 'state', 'the report moved on');
    await press($(`action-${msg}-accept`));
    await until(() => text().includes('the report moved on'), 'the reason', 3000);
    expect(stateOf(m)).toBe('review');
    m.store.stop();
  }, 20000);
});
