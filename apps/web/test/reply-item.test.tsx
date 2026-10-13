// Reply to an agent's own reply: the Reply control, the quote chip, and the quote kept with the sent message.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
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

const ask = (t: ReturnType<typeof setup>) => t.daemon.chat.seedItem(ARCH, { kind: 'reply', text: 'I would cut the third gap.' });
const stored = (t: ReturnType<typeof setup>, body: string) =>
  (
    t.daemon.chat as unknown as { messages: { body: string; parent: number | null; parent_item?: { id: number; text: string } }[] }
  ).messages.find((m) => m.body === body);
const replyBubble = () => $$('[data-kind="reply"]').find((r) => text(r).includes('I would cut the third gap.'));

describe('replying to an agent reply', () => {
  it('Reply sets the quote chip, Enter sends the item id once, and the sent bubble draws the quote', async () => {
    const t = setup();
    ask(t);
    await mount(ARCH, t);
    await until(() => replyBubble(), 'the reply bubble');
    await click(replyBubble()!);
    expect(text($('.compose-ref'))).toContain('I would cut the third gap.');
    await typeInto(compose(), 'why that one');
    await press(compose(), 'Enter');
    await until(() => stored(t, 'why that one'), 'the message');
    const m = stored(t, 'why that one')!;
    expect(m.parent).toBeNull();
    expect(m.parent_item?.text).toBe('I would cut the third gap.');
    await until(() => msg('why that one')?.querySelector('.msg-quote'), 'the quote');
    expect(text(msg('why that one')!.querySelector('.msg-quote'))).toContain('I would cut the third gap.');
    expect($('.compose-ref')).toBeNull();
  });

  it('Escape drops the pick, and picking a message instead drops the item', async () => {
    const t = setup();
    ask(t);
    await mount(ARCH, t);
    await until(() => replyBubble(), 'the reply bubble');
    await click(replyBubble()!);
    await press(compose(), 'Escape');
    expect($('.compose-ref')).toBeNull();
    await click(replyBubble()!);
    await click(msg('Which gap list')!);
    expect(text($('.compose-ref'))).toContain('Replying to #');
    await typeInto(compose(), 'both?');
    await press(compose(), 'Enter');
    await until(() => stored(t, 'both?'), 'the message');
    expect(stored(t, 'both?')!.parent_item).toBeUndefined();
  });

  it('cannot pick a reply to the agent text when the daemon serves no transcript', async () => {
    const t = setup({ daemon: { transcript: false } });
    ask(t);
    await mount(ARCH, t);
    await until(() => msg('Which gap list'), 'history');
    await click(replyBubble()!);
    expect($('.compose-ref')).toBeNull();
  });
});
