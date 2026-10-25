// Entering a chat: nothing is drawn before both the task cards and the chat have loaded, and leaving and coming back draws the conversation again.
import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, sleep, until } from '#test/harness.tsx';

const msgs = () => document.querySelectorAll('[data-testid^="msg-"]');

describe('entering a chat', () => {
  it('draws no message until the task cards have loaded too', async () => {
    let release = () => {};
    const m = await mount((s) => (release = s.daemon.knobs.hold('tasks.list')), undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('chat-input'), 'the chat');
    await sleep(600); // the chat read is long done; the task read is held
    expect(msgs()).toHaveLength(0);
    release();
    await until(() => msgs().length > 0, 'the messages', 6000);
    m.store.stop();
  });

  it('draws the conversation again on coming back', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => msgs().length > 0, 'the messages', 6000);
    const first = msgs().length;
    await press($('back'));
    await until(() => $(`agent-${ARCH}`), 'the rows again');
    await press($(`agent-${ARCH}`));
    await until(() => msgs().length >= first, 'the messages again', 6000);
    m.store.stop();
  });
});
