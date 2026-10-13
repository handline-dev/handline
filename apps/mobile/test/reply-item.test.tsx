// Reply to an agent's own reply on the phone: the Reply control, the quote chip, and the quote kept with the sent message.
import { describe, expect, it } from 'vitest';
import { act } from 'react';
import { ARCH, $, mount, press, text, until } from './harness.tsx';

const REPLY = 'I would cut the third gap.';
type Stored = { body: string; parent: number | null; parent_item?: { id: number; text: string } };
const stored = (m: Awaited<ReturnType<typeof mount>>, body: string) =>
  (m.daemon.chat as unknown as { messages: Stored[] }).messages.find((x) => x.body === body);
const type = (v: string) =>
  act(async () => {
    const input = $('chat-input') as unknown as HTMLTextAreaElement;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, v);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });

async function open(daemon?: Parameters<typeof mount>[1]) {
  const m = await mount((s) => s.daemon.chat.seedItem(ARCH, { kind: 'reply', text: REPLY }), daemon, 'founder');
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  return m;
}
const replyButton = () =>
  [...document.querySelectorAll<HTMLElement>('[data-testid^="reply-"]')]
    .find((r) => r.textContent?.includes(REPLY))
    ?.querySelector<HTMLElement>('[data-testid^="reply-to-"]') ?? null;

describe('replying to an agent reply', () => {
  it('Reply sets the quote chip, Send passes the item id once, and the sent bubble draws the quote', async () => {
    const m = await open();
    await until(replyButton, 'the reply control');
    await press(replyButton());
    expect(text($('replying-item'))).toContain(REPLY);
    await type('why that one');
    await press($('chat-send'));
    await until(() => stored(m, 'why that one'), 'the message');
    const sent = stored(m, 'why that one')!;
    expect(sent.parent).toBeNull();
    expect(sent.parent_item?.text).toBe(REPLY);
    await until(() => document.querySelector('[data-testid^="quote-"]'), 'the quote');
    expect(text(document.querySelector('[data-testid^="quote-"]'))).toContain(REPLY);
    expect($('replying-item')).toBeNull();
    m.store.stop();
  });

  it('drops the item when a message is picked, and the pick when Reply is pressed', async () => {
    const m = await open();
    await until(replyButton, 'the reply control');
    await press(replyButton());
    await press(document.querySelector<HTMLElement>('[data-testid^="msg-"]'));
    expect($('replying-item')).toBeNull();
    expect($('replying')).not.toBeNull();
    await press(replyButton());
    expect($('replying')).toBeNull();
    expect($('replying-item')).not.toBeNull();
    await press(replyButton()); // a second Reply on the same reply drops it, as on the web
    expect($('replying-item')).toBeNull();
    m.store.stop();
  });

  it('has no Reply control when the daemon serves no transcript', async () => {
    const m = await open({ transcript: false });
    await until(() => document.querySelector('[data-testid^="msg-"]'), 'history');
    expect(replyButton()).toBeNull();
    m.store.stop();
  });
});
