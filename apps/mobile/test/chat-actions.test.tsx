// The one-tap buttons on a chat message that waits on a decision (ChatMessage.actions), against the fake daemon: OK calls the verb the
// action names, No asks for a note first, and a refusal is shown on the message.
import { describe, expect, it } from 'vitest';
import type { setup } from '@handline/fake-daemon/helpers';
import { ARCH, $, mount, press, text, until } from './harness.tsx';

const TASK = 1800;
const seeded = (s: ReturnType<typeof setup>) => {
  s.daemon.tasks.verbs = true;
  s.daemon.tasks.seed({
    id: TASK,
    from: ARCH,
    to: 'founder',
    title: 'Founder task',
    state: 'doing',
  });
  const id = s.daemon.chat.seed(ARCH, {
    body: 'T1800 is yours. Is it fine to go ahead?',
    from: ARCH,
    to: 'founder',
    actions: [
      { key: 'ok', label: 'OK', verb: 'task.done', task: TASK, params: {} },
      { key: 'no', label: 'No', verb: 'task.handoff', task: TASK, params: { to: ARCH }, needs: 'note' },
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
  await until(() => $(`action-${msg}-ok`), 'the buttons');
  return m;
}

describe('one-tap actions on a chat message', () => {
  it('OK closes the task', async () => {
    const m = await openChat();
    await press($(`action-${msg}-ok`));
    await until(() => stateOf(m) === 'done', 'the done');
    m.store.stop();
  }, 20000);

  it('shows why when the daemon refuses', async () => {
    const m = await openChat();
    m.daemon.knobs.refuse('task.done', 'state', 'someone else decided it');
    await press($(`action-${msg}-ok`));
    await until(() => text().includes('someone else decided it'), 'the reason', 3000);
    expect(stateOf(m)).toBe('doing');
    m.store.stop();
  }, 20000);
});
