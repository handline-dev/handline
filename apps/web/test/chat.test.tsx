// U8: the Chat tab on app-data, against the fake daemon's chat with its latency and refusal knobs.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { RpcError } from '@handline/client';
import { second, setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const REFUSAL = 'the agent has no open session';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV & { data: Map<string, string> } => {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const typeInto = (el: HTMLTextAreaElement | HTMLInputElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const press = (el: Element, key: string, init: KeyboardEventInit = {}) =>
  act(async () => void el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })));
const compose = () => $('.compose') as HTMLTextAreaElement;
const msg = (body: string) => $$('.msg').find((m) => text(m).includes(body));
const landing = (body: string) => msg(body)?.getAttribute('data-landing');
const link = (label: string, within: Element) => [...within.querySelectorAll('button.link')].find((b) => text(b) === label)!;
const sent = (s: ReturnType<typeof setup>, body: string) =>
  (s.daemon.chat as unknown as { messages: { body: string }[] }).messages.some((m) => m.body === body);

async function mount(agent: string, s = setup(), storage: KV = memory()) {
  const store = new Store(s.client);
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={storage} />);
  });
  return { store, storage, ...s };
}
const unmount = async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
};

beforeEach(() => void vi.stubGlobal('confirm', () => true));
afterEach(async () => {
  await unmount();
  vi.unstubAllGlobals();
});

describe('the Chat tab', () => {
  it('says so when the daemon does not serve chat, and still has no way to send', async () => {
    await mount(ARCH, setup({ daemon: { chat: false } }));
    await until(() => $('.chat'), 'the chat tab');
    expect(text($('.chat'))).toContain('No chat yet');
    expect(compose().disabled).toBe(true);
    expect($('.chat-tabs')).toBeNull();
  });

  it("draws every message state and transcript kind from the daemon, with the agent's question answerable", async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    expect(landing('Draft the web chat tab plan.')).toBe('answered');
    expect(landing('Use the contract fixtures.')).toBe('delivered_unanswered');
    expect(text(msg('Use the contract fixtures.'))).toContain('Not answered');
    expect(landing('Hold that, scope changed.')).toBe('withdrawn');
    expect(landing('Also add the list of gaps.')).toBe('typed');
    expect(text(msg('Also add the list of gaps.'))).toContain('Withdraw');
    expect(text(msg('Draft the web chat tab plan.'))).not.toContain('Withdraw');
    expect($$('[data-kind="reply"]')).toHaveLength(1);
    expect($$('[data-kind="tool"]')).toHaveLength(1);
    expect(text($$('[data-kind="task_event"]')[1])).toContain('Task #7 done');
    expect(msg('Review is done')).toBeUndefined(); // agent to agent: the All tab
    await click($$('.chat-tab')[1]!);
    await until(() => msg('Review is done'), 'the all tab');
  });

  it('sends with Enter, shows the message landing, and keeps an unsent draft across a reload', async () => {
    const storage = memory();
    const t = await mount(ARCH, setup(), storage);
    await until(() => msg('Which gap list'), 'history');
    await typeInto(compose(), 'half a thought');
    await unmount();
    await mount(ARCH, { ...t, client: second(t.daemon, t.net) }, storage);
    await until(() => compose(), 'chat again');
    expect(compose().value).toBe('half a thought');
    await typeInto(compose(), 'ship it');
    await press(compose(), 'Enter', { shiftKey: true });
    expect(msg('ship it')).toBeUndefined(); // Shift+Enter is a new line, not a send
    await press(compose(), 'Enter');
    await until(() => msg('ship it'), 'the sent message');
    expect(compose().value).toBe('');
    await until(() => sent(t, 'ship it'), 'the daemon got it');
    t.daemon.chat.tick();
    await until(() => landing('ship it') === 'typed', 'typed');
    t.daemon.chat.tick();
    t.daemon.chat.tick();
    await until(() => text($('.working')).includes('Reading the code'), 'the working line');
    t.daemon.chat.tick();
    await until(() => landing('ship it') === 'answered', 'answered');
    expect($('.working')).toBeNull();
    await until(() => text($('.stream')).includes('Got it: "ship it"'), 'the reply');
    expect(storage.data.get(`handline.draft.${ARCH}`)).toBe('');
  });

  it('recalls what was sent with the up arrow, withdraws a message that has not landed, and answers a question', async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await press(compose(), 'ArrowUp');
    expect(compose().value).toBe('Also add the list of gaps.'); // the newest of mine
    await press(compose(), 'ArrowUp');
    expect(compose().value).toBe('Use the contract fixtures.');
    await press(compose(), 'ArrowDown');
    await press(compose(), 'ArrowDown');
    expect(compose().value).toBe('');
    await click(link('Withdraw', msg('Also add the list of gaps.')!));
    await until(() => landing('Also add the list of gaps.') === 'withdrawn', 'withdrawn');
    await typeInto($('.ask input') as HTMLInputElement, 'chat first');
    await click($('.ask .btn.primary')!);
    await until(() => text(msg('Which gap list')).includes('Your answer'), 'the answer shows');
    expect($('.ask')).toBeNull();
  });

  it('follows another screen through the chat topic', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    const other = second(t.daemon, t.net, { client: 'web-other' });
    await other.start();
    await other.call('chat.send', { agent: ARCH, body: 'from elsewhere' });
    await until(() => msg('from elsewhere'), 'the message from the other screen');
    for (let i = 0; i < 4; i++) t.daemon.chat.tick();
    await until(() => landing('from elsewhere') === 'answered', 'answered');
    other.close();
  });

  it('pages back through older messages', async () => {
    const s = setup();
    for (let i = 0; i < 60; i++)
      s.daemon.chat.seed(ARCH, { from: 'founder', to: ARCH, body: `old ${i}`, at: new Date(Date.UTC(2020, 0, 1, 0, i)).toISOString() });
    await mount(ARCH, s);
    await until(() => msg('old 59'), 'the newest page');
    expect(msg('Which gap list')).toBeUndefined(); // it is on the second page
    await click($('.older')!);
    await until(() => msg('Which gap list'), 'the older page');
    expect($('.older')).toBeNull(); // nothing further back
  });
});

describe('loading and writes with latency and refusals', () => {
  it('draws the conversation skeleton after the delay and no spinner, then the conversation', async () => {
    const s = setup();
    s.daemon.knobs.latency(400);
    await mount(ARCH, s);
    await until(() => $('.chat .skeleton'), 'the chat skeleton');
    expect($('.chat .skeleton')!.getAttribute('aria-busy')).toBe('true');
    expect($('.spinner, [role=progressbar]')).toBeNull();
    expect($$('.chat .sk').length).toBeGreaterThan(3);
    await until(() => msg('Which gap list'), 'the conversation', 600);
    await until(() => !$('.chat .skeleton'), 'the skeleton gone');
  });

  it('shows a sent message at once, marked pending and without Withdraw, and clears the box before the daemon answers', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    s.daemon.knobs.latency(500);
    await typeInto(compose(), 'right now');
    await press(compose(), 'Enter');
    await until(() => msg('right now'), 'the message');
    expect(sent(s, 'right now')).toBe(false); // the daemon has not answered yet
    expect(landing('right now')).toBe('queued');
    expect(msg('right now')!.getAttribute('data-pending')).toBe('true');
    expect(text(msg('right now'))).toContain('Sending');
    expect(text(msg('right now'))).not.toContain('Withdraw'); // it has no number yet
    expect(compose().value).toBe('');
    await until(() => sent(s, 'right now'), 'the daemon got it', 600);
    await until(() => $$('.msg').filter((m) => text(m).includes('right now')).length === 1, 'the real message replaced the temporary one');
    expect(msg('right now')!.getAttribute('data-pending')).toBeNull();
  });

  it('keeps a refused message as a failed row with its words and the reason; Retry sends it again', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    s.daemon.knobs.refuse('chat.send', 'state', REFUSAL, undefined, 1);
    await typeInto(compose(), 'will not land');
    await press(compose(), 'Enter');
    await until(() => $('.msg.failed'), 'the failed row');
    expect(text($('.msg.failed'))).toContain('will not land');
    expect(text($('.msg.failed'))).toContain(REFUSAL);
    expect(compose().value).toBe('');
    await click(link('Retry', $('.msg.failed')!));
    await until(() => sent(s, 'will not land'), 'the retry reached the daemon');
    await until(() => !$('.msg.failed'), 'the failed row gone');
    expect($$('.msg').filter((m) => text(m).includes('will not land'))).toHaveLength(1);
  });

  it('Discard drops a failed row', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    s.daemon.knobs.refuse('chat.send', 'state', REFUSAL);
    await typeInto(compose(), 'drop me');
    await press(compose(), 'Enter');
    await until(() => $('.msg.failed'), 'the failed row');
    await click(link('Discard', $('.msg.failed')!));
    await until(() => !msg('drop me'), 'the row gone');
    expect(sent(s, 'drop me')).toBe(false);
  });

  it('puts a refused withdraw back as it was, with the reason', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => msg('Also add the list of gaps.'), 'history');
    s.daemon.knobs.latency(500);
    s.daemon.knobs.refuse('chat.withdraw', 'state', REFUSAL);
    await click(link('Withdraw', msg('Also add the list of gaps.')!));
    await until(() => landing('Also add the list of gaps.') === 'withdrawn', 'withdrawn at once'); // before the daemon answers
    await until(() => landing('Also add the list of gaps.') === 'typed', 'rolled back', 600);
    expect(text($('.toast'))).toContain(REFUSAL);
  });

  it('puts a refused answer back as an open question, with the reason', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => $('.ask input'), 'the open question');
    s.daemon.knobs.latency(500);
    s.daemon.knobs.refuse('chat.answer', 'state', REFUSAL);
    await typeInto($('.ask input') as HTMLInputElement, 'chat first');
    await click($('.ask .btn.primary')!);
    await until(() => !$('.ask') && text(msg('Which gap list')).includes('Your answer'), 'answered at once');
    await until(() => $('.ask'), 'the question open again', 600);
    expect(text(msg('Which gap list'))).not.toContain('Your answer');
    expect(text($('.toast'))).toContain(REFUSAL);
  });

  it('puts a refused dismiss back, with the reason', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => $('.ask'), 'the open question');
    s.daemon.knobs.latency(500);
    s.daemon.knobs.refuse('chat.dismiss', 'state', REFUSAL);
    await click($$('.ask .btn').find((b) => text(b) === 'Dismiss')!);
    await until(() => !$('.ask'), 'dismissed at once');
    await until(() => $('.ask'), 'the question open again', 600);
    expect(text($('.toast'))).toContain(REFUSAL);
  });
});

describe('errors', () => {
  it('says so when the conversation cannot be read, with Retry, and not a skeleton forever', async () => {
    const s = setup();
    s.daemon.knobs.refuse('chat.messages', 'state', REFUSAL);
    await mount(ARCH, s);
    await until(() => $('.chat .banner'), 'the error state');
    expect(text($('.chat .banner'))).toContain(REFUSAL);
    expect($('.chat .skeleton')).toBeNull();
    s.daemon.knobs.clear();
    await click($$('.chat .banner .btn').find((b) => text(b) === 'Retry')!);
    await until(() => msg('Which gap list'), 'the conversation after Retry');
    expect($('.chat .banner')).toBeNull();
  });

  it('keeps what it had when a later read fails', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    s.daemon.knobs.refuse('chat.messages', 'state', REFUSAL);
    s.daemon.dropConnections(); // the reconnect reads every active query again, and the read is refused
    await until(() => $('.chat .banner'), 'the error after a failed read');
    expect(msg('Which gap list')).toBeDefined(); // the last data stays
  });

  it("shows the daemon's sentence and its hint when a read is refused", async () => {
    const s = setup();
    s.daemon.knobs.refuse('chat.transcript', 'state', REFUSAL, 'start the agent first');
    await mount(ARCH, s);
    await until(() => $('.chat .banner'), 'the error state');
    expect(text($('.chat .banner'))).toContain(`${REFUSAL} (start the agent first)`);
  });

  it('draws no block when the read failed only because the connection is gone', async () => {
    const s = setup();
    const { store } = await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    const call = store.client.call.bind(store.client);
    vi.spyOn(store.client, 'call').mockImplementation(((method: string, params: never) =>
      method.startsWith('chat.') ? Promise.reject(new RpcError('transport', 'closed')) : call(method as never, params)) as never);
    s.daemon.dropConnections(); // the reconnect reads again and the read cannot reach the daemon
    await wait(300);
    expect($('.chat .banner')).toBeNull();
    expect(msg('Which gap list')).toBeDefined();
  });
});
