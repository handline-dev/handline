import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { ARCH, sleep, until, box, $, text, press, mount, pickTab } from '#test/harness.tsx';

describe('the chat tab', () => {
  const type = (id: string, value: string) =>
    act(async () => {
      const el = $(id) as HTMLTextAreaElement | HTMLInputElement;
      Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  const msgOf = (body: string) =>
    [...box.querySelectorAll<HTMLElement>('[data-testid^="msg-"]')].find((e) => e.textContent?.includes(body));
  const tick = (e: HTMLElement | undefined) => e?.querySelector('[data-testid^="landing-"]')?.getAttribute('aria-label');
  async function openChat() {
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('chat-input'), 'the chat');
  }

  it('shows its skeleton while the chat read is slow, then the rows', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    m.daemon.knobs.latency(500);
    await press($(`agent-${ARCH}`));
    await until(() => $('skeleton-chat'), 'the chat skeleton', 5000);
    expect(msgOf('Use the contract fixtures.')).toBeUndefined();
    await until(() => msgOf('Use the contract fixtures.'), 'the messages', 5000);
    await sleep(400); // the skeleton stays at least 300 ms once shown
    expect($('skeleton-chat')).toBeNull();
    m.store.stop();
  });

  it('opens a file path and a task number in a message as tabs beside the chat', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await type('chat-input', 'see notes/plan.md and T1684');
    await press($('chat-send'));
    const find = (kind: string) =>
      box.querySelector<HTMLElement>(`[data-testid^="${kind}-"][data-testid$="${kind === 'file' ? 'notes/plan.md' : '-1684'}"]`);
    const link = async (kind: string) => (await until(() => find(kind), `the ${kind} link`), find(kind));
    await press(await link('file'));
    await until(() => $('dock-file:notes/plan.md'), 'the file dock tab');
    await until(() => $('file-tab'), 'the file tab');
    await press($('dock-base'));
    await press(await link('task'));
    await until(() => $('dock-task:1684') && $('task-detail'), 'the task tab');
    expect($('dock-file:notes/plan.md')).not.toBeNull();
    m.store.stop();
  });

  it('shows the daemon’s sentence when the chat read is refused, and Retry reads again', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    m.daemon.knobs.refuse('chat.messages', 'state', 'the daemon is resting', 'try again later');
    await press($(`agent-${ARCH}`));
    await until(() => $('error'), 'the error block');
    expect($('error')!.textContent).toContain('the daemon is resting (try again later)');
    expect(msgOf('Use the contract fixtures.')).toBeUndefined();
    m.daemon.knobs.clear();
    await press([...box.querySelectorAll<HTMLElement>('[role="button"]')].find((b) => b.textContent === 'Retry') ?? null);
    await until(() => msgOf('Use the contract fixtures.'), 'the messages after Retry');
    expect($('error')).toBeNull();
    m.store.stop();
  });

  it('shows the conversation with how far each message got', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await until(() => msgOf('Use the contract fixtures.'), 'the messages');
    expect(tick(msgOf('Use the contract fixtures.')!)).toBe('Answered');
    expect(tick(msgOf('Draft the web chat tab plan.')!)).toBe('Answered');
    expect(text()).not.toContain('Delivered'); // ticks, no word
    expect(msgOf('Use the contract fixtures.')!.textContent).not.toContain('#'); // no message numbers on screen
    expect(text()).toContain('Here is the plan: three units, chat first.'); // the agent's reply
    expect(msgOf('Review is done, see the notes.')).toBeUndefined();
    expect($('scope-all')).toBeNull(); // no Mine/All menu
    m.store.stop();
  });

  it('sends at once, follows the message to delivered, and shows the working line', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await type('chat-input', 'run the tests');
    await press($('chat-send'));
    await until(() => msgOf('run the tests'), 'the sent message');
    expect(($('chat-input') as HTMLTextAreaElement).value).toBe('');
    await act(async () => void [1, 2, 3].forEach(() => m.daemon.tick())); // typed, delivered, thinking
    await until(() => $('working'), 'the working line');
    expect($('working')!.textContent).toContain('now: Read src/main.ts');
    expect(tick(msgOf('run the tests')!)).toBe('Answered');
    m.store.stop();
  });

  it('keeps a refused message as a failed row with the reason and Retry, and sends it again when the daemon allows', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('chat.send', 'state', 'the daemon is resting', undefined, 1), undefined, 'founder');
    await openChat();
    await type('chat-input', 'please retry me');
    await press($('chat-send'));
    await until(() => msgOf('please retry me')?.textContent?.includes('the daemon is resting'), 'the failed row');
    const row = msgOf('please retry me')!;
    await press(row.querySelector<HTMLElement>('[data-testid^="retry-"]'));
    await until(() => tick(msgOf('please retry me')) === 'Sending', 'the retried message');
    expect(msgOf('please retry me')!.textContent).not.toContain('the daemon is resting');
    m.store.stop();
  });

  it('replies to a picked message with its number', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await until(() => msgOf('Use the contract fixtures.'), 'the messages');
    await press(msgOf('Use the contract fixtures.')!);
    expect($('replying')!.textContent).toContain('Use the contract fixtures.');
    await type('chat-input', 'about that');
    await press($('chat-send'));
    await until(() => msgOf('about that'), 'the reply');
    expect(msgOf('about that')!.textContent).toContain('↩ reply');
    expect($('replying')).toBeNull();
    m.store.stop();
  });

  it('a picked message is a reference: it is sent as a linked message', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    const q = 'Which gap list should come first?';
    await until(() => msgOf(q), 'the question');
    await press(msgOf(q)!);
    await type('chat-input', 'web first');
    await press($('chat-send'));
    await until(() => msgOf('web first'), 'the reply');
    expect(msgOf('web first')!.textContent).toContain('↩ reply');
    m.store.stop();
  });

  it('a question from the agent is an ordinary message: no answer box, no Dismiss, no waiting mark', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await until(() => msgOf('Which gap list should come first?'), 'the question');
    const el = msgOf('Which gap list should come first?')!;
    const id = Number(el.getAttribute('data-testid')!.slice(4));
    expect($(`answer-input-${id}`)).toBeNull();
    expect($(`dismiss-${id}`)).toBeNull();
    expect(el.textContent).not.toContain('Waiting for you');
    m.store.stop();
  });

  it('keeps an unsent draft per agent while the person is on another tab', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await type('chat-input', 'half a thought');
    await pickTab('terminal');
    await pickTab('chat');
    await until(() => $('chat-input'), 'the chat again');
    expect(($('chat-input') as HTMLTextAreaElement).value).toBe('half a thought');
    m.store.stop();
  });

  it('says so when the daemon does not serve chat', async () => {
    const m = await mount(() => {}, { chat: false }, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('chat-off'), 'the empty state');
    expect($('chat-input')).toBeNull();
    m.store.stop();
  });
});
