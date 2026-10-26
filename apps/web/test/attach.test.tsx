// Images in the send box: a paste, a drop or the picker makes a chip that uploads; Send names the finished ones; the sent message draws them.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

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

const PNG = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), (c) =>
  c.charCodeAt(0),
);
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

  it('keeps the attached images when the person leaves the chat and comes back, until they are sent or removed', async () => {
    await mount();
    await paste([image('a.png'), image('b.png')]);
    await until(() => chips().length === 2 && chips().every((c) => c.getAttribute('data-state') === 'ready'), 'both uploaded');
    await click($('[data-tab="transcript"]')!);
    await until(() => !compose(), 'the send box gone');
    await click($('[data-tab^="chat"]')!);
    await until(() => compose(), 'the send box again');
    expect(chips().map((c) => text(c))).toEqual([expect.stringContaining('a.png'), expect.stringContaining('b.png')]);
    expect(sendBtn().disabled).toBe(false);
    await click(chips()[0]!.querySelector('button[aria-label^="Remove"]')!);
    await click($('[data-tab="transcript"]')!);
    await click($('[data-tab^="chat"]')!);
    await until(() => compose(), 'the send box once more');
    expect(chips().map((c) => text(c))).toEqual([expect.stringContaining('b.png')]);
    await click(sendBtn());
    await until(() => $$('.msg .attachment img').length === 1, 'the picture');
    await click($('[data-tab="transcript"]')!);
    await click($('[data-tab^="chat"]')!);
    await until(() => compose(), 'the send box after sending');
    expect(chips()).toHaveLength(0);
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

describe('a folder or file dropped from Finder', () => {
  const drop = (files: File[]) =>
    act(async () => {
      const e = new Event('drop', { bubbles: true, cancelable: true });
      Object.defineProperty(e, 'dataTransfer', { value: { files, types: ['Files'] } });
      compose().dispatchEvent(e);
    });
  const folder = (name: string) => Object.assign(new File([], name), { fakePath: `/Users/me/${name}` });
  const withShell = (on: boolean, bringIn?: unknown) => {
    const w = window as unknown as { handline?: unknown };
    if (on)
      w.handline = { api: 1, pathOf: (f: File & { fakePath?: string }) => f.fakePath ?? '', bringIn, onNavigate: () => () => undefined };
    else delete w.handline;
  };
  afterEach(() => withShell(false));

  it('is a chip with its name; Send gives the agent the absolute path, and a removed chip is not sent', async () => {
    withShell(true);
    const s = await mount();
    await drop([folder('proj'), folder('notes.md')]);
    expect(chips().map(text).join()).toContain('proj');
    expect(chips()).toHaveLength(2);
    await click(chips()[1]!.querySelector('button')!);
    expect(chips()).toHaveLength(1);
    await typeInto(compose(), 'look at this');
    await click(sendBtn());
    await until(() => chips().length === 0, 'sent');
    const mine = (await s.client.call('chat.messages', { agent: AUD, scope: 'mine' })).messages.map((m) => m.body);
    expect(mine).toContain('look at this\n/Users/me/proj');
    expect(mine.join()).not.toContain('notes.md');
  });

  it('is sent as the copy the shell made in the agent folder; when the copy fails the original path is sent', async () => {
    withShell(true, async (a: { path: string }) =>
      a.path.endsWith('bad')
        ? { ok: false, code: 'unavailable', message: 'x' }
        : { ok: true, path: `/wt/.handline-drops/d-1/${a.path.split('/').pop()}` },
    );
    const s = await mount();
    await drop([folder('notes.md'), folder('bad')]);
    await until(() => chips().length === 2, 'chips');
    await typeInto(compose(), 'see');
    await click(sendBtn());
    await until(() => chips().length === 0, 'sent');
    const mine = (await s.client.call('chat.messages', { agent: AUD, scope: 'mine' })).messages.map((m) => m.body);
    expect(mine).toContain('see\n/wt/.handline-drops/d-1/notes.md\n/Users/me/bad');
  });

  it('is ignored where the shell cannot say the path', async () => {
    withShell(false);
    await mount();
    await drop([folder('proj')]);
    expect(chips()).toHaveLength(0);
  });
});
