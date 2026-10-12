// Images in the send box: a paste, a drop or the picker makes a chip that uploads; Send names the finished ones; the sent message draws them.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const AUD = 'handline.auditor'; // a structured session
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


const PNG = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), (c) => c.charCodeAt(0));
const image = (name = 'shot.png', type = 'image/png') => new File([PNG], name, { type });
const chips = () => $$('.attach-chip');
const paste = (files: File[]) =>
  act(async () => {
    const e = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(e, 'clipboardData', { value: { files } });
    compose().dispatchEvent(e);
  });
const sendBtn = () => $('.compose-send') as HTMLButtonElement;

async function mount(s = setup()) {
  const store = new Store(s.client);
  location.hash = `#/agents/${AUD}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
  await until(() => $('[data-tab="transcript"]'), 'the watching tab');
  await click($('[data-tab^="chat"]')!);
  await until(() => compose() && !compose().disabled, 'the send box');
  return s;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

const revoked: string[] = [];

describe('images in the send box', () => {
  beforeEach(() => {
    let n = 0;
    URL.createObjectURL = () => `blob:test/${++n}`;
    URL.revokeObjectURL = (u) => void revoked.push(u);
    revoked.length = 0;
  });

  it('a pasted image is a chip that uploads, then Send (with no words) draws it in the message', async () => {
    const s = await mount();
    expect(sendBtn().disabled).toBe(true);
    await paste([image()]);
    await until(() => chips()[0]?.getAttribute('data-state') === 'ready', 'uploaded');
    expect(text(chips()[0])).toContain('shot.png');
    expect(sendBtn().disabled).toBe(false);
    await click(sendBtn());
    await until(() => $$('.msg .attachment img').length, 'the picture in the message');
    expect(chips()).toHaveLength(0);
    const mine = (await s.client.call('chat.messages', { agent: AUD, scope: 'mine' })).messages.filter((m) => m.attachments?.length);
    expect(mine).toHaveLength(1);
    expect(mine[0]!.attachments![0]!.name).toBe('shot.png');
  });

  it('under a picked question the words and the images go as one message that replies to it', async () => {
    const s = setup();
    const id = s.daemon.chat.ask(AUD, 'Which of the two?', 'founder');
    await mount(s);
    await until(() => msg('Which of the two?'), 'the question');
    await click(msg('Which of the two?')!);
    await typeInto(compose(), 'The second.');
    await paste([image()]);
    await until(() => chips()[0]?.getAttribute('data-state') === 'ready', 'uploaded');
    await click(sendBtn());
    await until(() => $$('.msg .attachment img').length, 'the picture');
    const all = (await s.client.call('chat.messages', { agent: AUD, scope: 'all' })).messages;
    const sent = all.find((m) => m.attachments?.length)!;
    expect(sent.parent).toBe(id);
    expect(sent.body).toBe('The second.');
    expect(sent.state).not.toBe('failed');
  });

  it('a chip is removed before sending, and the message goes without it', async () => {
    await mount();
    await paste([image('a.png'), image('b.png')]);
    await until(() => chips().length === 2 && chips().every((c) => c.getAttribute('data-state') === 'ready'), 'both uploaded');
    await click(chips()[0]!.querySelector('button[aria-label^="Remove"]')!);
    expect(chips().map((c) => text(c))).toEqual([expect.stringContaining('b.png')]);
    await typeInto(compose(), 'with one');
    await press(compose(), 'Enter');
    await until(() => $$('.msg .attachment img').length === 1, 'one picture');
  });

  it('says why a file that is not an image, or too many images, are not attached', async () => {
    await mount();
    await paste([new File(['x'], 'notes.png', { type: 'image/svg+xml' })]);
    expect(text($('.attach-chips'))).toContain('only PNG, JPEG, GIF and WebP');
    expect(chips()).toHaveLength(0);
    await paste(Array.from({ length: 7 }, (_, i) => image(`i${i}.png`)));
    expect(chips()).toHaveLength(6);
    expect(text($('.attach-chips'))).toContain('At most 6');
  });

  it('lets go of a preview when its chip is removed or the message is sent', async () => {
    await mount();
    await paste([image('a.png'), image('b.png')]);
    await until(() => chips().length === 2 && chips().every((c) => c.getAttribute('data-state') === 'ready'), 'both uploaded');
    const [a, b] = chips().map((c) => c.querySelector('img')!.getAttribute('src')!);
    await click(chips()[0]!.querySelector('button[aria-label^="Remove"]')!);
    expect(revoked).toEqual([a]);
    await click(sendBtn());
    await until(() => chips().length === 0, 'sent');
    expect(revoked).toEqual([a, b]);
  });
});
