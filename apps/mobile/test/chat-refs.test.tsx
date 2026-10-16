import { describe, expect, it } from 'vitest';
import { ARCH, $, fill, mount, press, until } from './harness.tsx';

async function openChat() {
  const m = await mount(() => {}, undefined, 'founder');
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $('chat-input'), 'the chat');
  return m;
}

describe('# in the compose box', () => {
  it('lists the rows of chat.refs, and a pick sends a linked message', async () => {
    const m = await openChat();
    const { requests } = await m.daemon.chat.refs({ agent: ARCH });
    const first = requests[0]!;
    await fill('chat-input', 'About #');
    await until(() => $(`ref-${first.request}`), 'the list');
    await press($(`ref-${first.request}`));
    await until(() => $('replying'), 'the reference');
    expect($('replying')!.textContent).toContain(`#${first.request}`);
    expect(($('chat-input') as unknown as HTMLTextAreaElement).value).toBe('About ');
    await fill('chat-input', 'About that');
    await press($('chat-send'));
    // The message goes to whoever said the request, so it is in that agent's chat; the daemon's list has it.
    await until(
      () =>
        m.daemon.chat
          .refs({ agent: first.from === ARCH || first.from === 'founder' ? ARCH : first.from })
          .requests.some((r) => r.line === 'About that'),
      'the sent message',
    );
    m.store.stop();
  }, 20000);

  it('shows no list when the daemon does not serve chat.refs', async () => {
    const m = await mount((s) => s.daemon.knobs.unserve('chat.refs'), undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('chat-input'), 'the chat');
    await fill('chat-input', '#');
    expect($('chat-refs')).toBeNull();
    m.store.stop();
  }, 20000);
});
