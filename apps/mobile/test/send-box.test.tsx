import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { $, mount, press, text, until } from './harness.tsx';

const AUD = 'handline.auditor'; // the fake daemon plays it as a structured session

async function open(running = false) {
  const m = await mount();
  await until(() => $(`agent-${AUD}`), 'the rows');
  await press($(`agent-${AUD}`));
  await until(() => $('tab-chat'), 'the page');
  await press($('tab-chat'));
  await until(() => $('chat-input'), 'the chat');
  if (running) m.daemon.sessions.get(AUD)!.state = 'running'; // a turn runs: the daemon queues what follows
  return m;
}
const type = async (v: string) => {
  const input = $('chat-input') as unknown as HTMLInputElement;
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set ?? Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    set?.call(input, v);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
};
const row = (body: string) => [...document.body.querySelectorAll<HTMLElement>('[data-testid^="msg-"]')].find((e) => e.textContent?.includes(body));
const landing = (body: string) => row(body)?.querySelector('[data-testid^="landing-"]')?.textContent;

describe('the send box over the one send path', () => {
  it('a message to an idle structured agent shows Sent, then Confirmed', async () => {
    const m = await open();
    expect($('chat-send-now')).toBeNull();
    await type('hello');
    await press($('chat-send'));
    await until(() => landing('hello')?.includes('Sent'), 'sent');
    await act(async () => void m.daemon.chat.tick());
    await until(() => landing('hello')?.includes('Confirmed'), 'confirmed');
    m.store.stop();
  });

  it('a queued message is written by Send now and a queued one is taken back without asking', async () => {
    const m = await open(true);
    await type('hurry');
    await press($('chat-send'));
    await until(() => landing('hurry')?.includes('Queued'), 'queued');
    const id = row('hurry')!.dataset.testid!.slice(4);
    expect($(`takeback-${id}`)!.textContent).toBe('Take back');
    await press($(`now-${id}`));
    await until(() => landing('hurry')?.includes('Sent'), 'sent');
    await type('oops');
    await press($('chat-send'));
    await until(() => landing('oops')?.includes('Queued'), 'queued');
    const oops = row('oops')!.dataset.testid!.slice(4);
    await press($(`takeback-${oops}`));
    await until(() => !row('oops'), 'withdrawn');
    expect(($('chat-input') as unknown as HTMLInputElement).value).toBe('oops');
    m.store.stop();
  });

  it('a message lost after it was written is unknown, says so, and is not sent again', async () => {
    const m = await open();
    await type('in flight');
    await press($('chat-send'));
    await until(() => landing('in flight')?.includes('Sent'), 'sent');
    await act(async () => {
      m.daemon.chat.lose(AUD);
      m.daemon.chat.tick();
    });
    await until(() => landing('in flight')?.includes('May or may not have run'), 'unknown');
    const id = row('in flight')!.dataset.testid!.slice(4);
    expect($(`unknown-${id}`)!.textContent).toContain('not sent again');
    expect($(`retry-${id}`)).toBeNull();
    expect($(`takeback-${id}`)).not.toBeNull(); // asks first, as for a delivered message
    expect(text().match(/in flight/g)).toHaveLength(1);
    m.store.stop();
  });
});
