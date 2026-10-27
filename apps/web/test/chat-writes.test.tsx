// The Chat tab's loading, writes and errors, against the fake daemon's latency and refusal knobs.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { RpcError } from '@handline/client';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

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
const memory = (): KV => {
  const data = new Map<string, string>();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
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
const press = (el: Element, key: string) =>
  act(async () => void el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })));
const compose = () => $('.compose') as HTMLTextAreaElement;
const msg = (body: string) => $$('.msg').find((m) => text(m).includes(body));
const landing = (body: string) => msg(body)?.getAttribute('data-landing');
const link = (label: string, within: Element) =>
  [...within.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === label)!;
const icon = (m: Element | undefined, label: string) => m?.querySelector(`[aria-label="${label}"]`) ?? null;
const sent = (s: ReturnType<typeof setup>, body: string) =>
  (s.daemon.chat as unknown as { messages: { body: string }[] }).messages.some((m) => m.body === body);

async function mount(agent: string, s = setup(), storage: KV = memory()) {
  const store = new Store(s.client);
  history.replaceState(null, '', location.pathname);
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={storage} />);
  });
  return { store, storage, ...s };
}

beforeEach(() => void vi.stubGlobal('confirm', () => true));
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  vi.unstubAllGlobals();
});

describe('loading and writes with latency and refusals', () => {
  it('draws the conversation skeleton after the delay and no spinner, then the conversation', async () => {
    const s = setup();
    const release = s.daemon.knobs.hold('chat.messages');
    await mount(ARCH, s);
    await until(() => $('.chat .skeleton'), 'the chat skeleton');
    expect($('.chat .skeleton')!.getAttribute('aria-busy')).toBe('true');
    expect($('.spinner, [role=progressbar]')).toBeNull();
    expect($$('.chat .sk').length).toBeGreaterThan(3);
    expect($$('.chat .msg, .chat [data-kind]')).toHaveLength(0); // nothing is drawn beside the skeleton
    release();
    await until(() => msg('Which gap list'), 'the conversation', 600);
    await until(() => !$('.chat .skeleton'), 'the skeleton gone');
  });

  it('shows a sent message at once, marked pending, and clears the box before the daemon answers', async () => {
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
    expect(icon(msg('right now')?.parentElement ?? undefined, 'Sending')).not.toBeNull();
    expect(compose().value).toBe('');
    await until(() => sent(s, 'right now'), 'the daemon got it', 600);
    // The temporary row is one row too: the real one has replaced it only when no row is pending.
    await until(() => {
      const rows = $$('.msg').filter((m) => text(m).includes('right now'));
      return rows.length === 1 && rows[0]!.getAttribute('data-pending') === null;
    }, 'the real message replaced the temporary one');
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
    await click($$('.chat .banner .ui-btn').find((b) => text(b) === 'Retry')!);
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
