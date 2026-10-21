import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { $, mount, press, text, until, pickTab } from '#test/harness.tsx';

const AUD = 'handline.auditor'; // the fake daemon plays it as a structured session

async function open(running = false) {
  const m = await mount();
  await until(() => $(`agent-${AUD}`), 'the rows');
  await press($(`agent-${AUD}`));
  await pickTab('chat');
  await until(() => $('chat-input'), 'the chat');
  if (running) {
    // A turn runs: the working line shows it and the daemon queues what follows.
    m.daemon.sessions.get(AUD)!.state = 'running';
    await act(async () => m.daemon.chat.setTurn(AUD, true));
    await until(() => $('working') && $('chat-send-now'), 'the turn running');
  }
  return m;
}
const type = async (v: string) => {
  const input = $('chat-input') as unknown as HTMLInputElement;
  await act(async () => {
    const set =
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set ??
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    set?.call(input, v);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
};
const row = (body: string) =>
  [...document.body.querySelectorAll<HTMLElement>('[data-testid^="msg-"]')].find((e) => e.textContent?.includes(body));
const landing = (body: string) => row(body)?.querySelector('[data-testid^="landing-"]')?.getAttribute('aria-label');

describe('the send box over the one send path', () => {
  it('a message to an idle structured agent shows one tick, then two', async () => {
    const m = await open();
    expect($('chat-send-now')).toBeNull();
    await type('hello');
    await press($('chat-send'));
    await until(() => landing('hello') === 'Delivered', 'sent');
    await act(async () => void m.daemon.chat.tick());
    await until(() => landing('hello') === 'Answered', 'confirmed');
    m.store.stop();
  });

  it('while a turn runs the person picks Queue or Send now', async () => {
    const m = await open(true);
    await type('later');
    await press($('chat-send'));
    await until(() => landing('later') === 'Sending', 'queued');
    await type('right now');
    await press($('chat-send-now'));
    await until(() => landing('right now') === 'Delivered', 'written at once');
    m.store.stop();
  });

  it('a terminal agent has no choice to make', async () => {
    const m = await mount();
    await until(() => $('agent-handline.architect'), 'the rows');
    await press($('agent-handline.architect'));
    await pickTab('chat');
    await until(() => $('chat-input'), 'the chat');
    expect($('chat-send-now')).toBeNull();
    m.store.stop();
  });

  it('a queued message is written by Send now', async () => {
    const m = await open(true);
    await type('hurry');
    await press($('chat-send'));
    await until(() => landing('hurry') === 'Sending', 'queued');
    const id = row('hurry')!.dataset.testid!.slice(4);
    expect($(`takeback-${id}`)).toBeNull();
    await press($(`now-${id}`));
    await until(() => landing('hurry') === 'Delivered', 'sent');
    m.store.stop();
  });

  it('a message lost after it was written is unknown, says so, and is not sent again', async () => {
    const m = await open();
    await type('in flight');
    await press($('chat-send'));
    await until(() => landing('in flight') === 'Delivered', 'sent');
    await act(async () => {
      m.daemon.chat.lose(AUD);
      m.daemon.chat.tick();
    });
    await until(() => landing('in flight') === 'May or may not have run', 'unknown');
    const id = row('in flight')!.dataset.testid!.slice(4);
    expect($(`unknown-${id}`)!.textContent).toContain('not sent again');
    expect($(`retry-${id}`)).toBeNull();
    expect(text().match(/in flight/g)).toHaveLength(1);
    m.store.stop();
  });
});
