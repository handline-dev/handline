import { describe, expect, it } from 'vitest';
import { ARCH, $, fill, mount, press, until } from '#test/harness.tsx';

async function openChat() {
  const m = await mount(() => {}, undefined, 'founder');
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $('chat-input'), 'the chat');
  return m;
}

describe('! in the compose box', () => {
  it('starts command mode: the hint shows, attaching is off, and a plain line has neither', async () => {
    const m = await openChat();
    await fill('chat-input', 'ls');
    expect($('cmd-hint')).toBeNull();
    expect($('attach-image')?.getAttribute('aria-disabled')).not.toBe('true');
    await fill('chat-input', '!ls');
    await until(() => $('cmd-hint'), 'the command hint');
    expect($('cmd-hint')!.textContent).toBe('Sent as typed to the agent');
    expect($('attach-image')?.getAttribute('aria-disabled')).toBe('true');
    m.store.stop();
  }, 20000);

  it('sends a command as typed with no reply link, even with a message picked', async () => {
    const m = await openChat();
    const first = () => document.querySelector('[data-testid^="msg-"]') as HTMLElement | null;
    await until(first, 'a message');
    await press(first());
    await until(() => $('replying'), 'the picked reply');
    await fill('chat-input', '!echo hi');
    await press($('chat-send'));
    const sent = () => [...document.querySelectorAll('[data-testid^="msg-"]')].find((e) => e.textContent?.includes('!echo hi'));
    await until(sent, 'the sent command');
    expect(sent()!.textContent).not.toContain('↩ reply');
    m.store.stop();
  }, 20000);
});
