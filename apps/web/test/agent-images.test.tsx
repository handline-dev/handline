// An agent's message with images (hl say --attach): the pictures stand inline between the words, a click opens one larger with a Close button,
// and an image that cannot be read says so in words; against the fake daemon.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 400; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV => {
  const data = new Map<string, string>();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => document.querySelector<HTMLElement>(sel);
const click = (el: Element) => act(async () => void (el as HTMLElement).click());

async function mount(s: ReturnType<typeof setup>) {
  const store = new Store(s.client);
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
}

/** One agent message: words, an image, more words, a second image. */
function seed(s: ReturnType<typeof setup>) {
  const a = s.daemon.uploads.addMemoFile('first.png', 'image/png', PNG);
  const b = s.daemon.uploads.addMemoFile('second.png', 'image/png', PNG);
  const img = (f: typeof a) => ({ id: f.id, name: f.name, mime: 'image/png' as const, bytes: f.bytes });
  s.daemon.chat.seed(ARCH, {
    from: ARCH,
    to: 'human',
    body: 'Before the shot ![one](attachment:' + a.id + ') between ![two](attachment:' + b.id + ') after',
    attachments: [img(a), img(b)],
    parts: [
      { text: 'Before the shot ' },
      { image: img(a), caption: 'one' },
      { text: ' between ' },
      { image: img(b), caption: 'two' },
      { text: ' after' },
    ],
  });
  return [a, b];
}

beforeEach(() => {
  let n = 0;
  URL.createObjectURL = () => `blob:test/${++n}`;
  URL.revokeObjectURL = () => {};
});
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('images an agent attaches to a message', () => {
  it('draws each picture in the message between its words, in order', async () => {
    const s = setup();
    seed(s);
    await mount(s);
    await until(() => box.querySelectorAll('.msg .attachment img').length === 2, 'both pictures');
    const msg = box.querySelector('.msg .attachment img')!.closest('.msg')!;
    expect(msg.textContent).toContain('Before the shot');
    expect(msg.textContent!.indexOf('Before')).toBeLessThan(msg.textContent!.indexOf('between'));
    expect(msg.textContent!.indexOf('between')).toBeLessThan(msg.textContent!.indexOf('after'));
    const imgs = [...msg.children].map((c) => (c.querySelector('img') ? 'img' : c.textContent?.trim() ? 'text' : ''));
    expect(imgs.filter(Boolean).join(',')).toContain('text,img,text,img,text');
  });

  it('opens a picture larger on a click, with a Close button that closes it', async () => {
    const s = setup();
    seed(s);
    await mount(s);
    await until(() => box.querySelectorAll('.msg .attachment img').length === 2, 'the pictures');
    await click(box.querySelector('.msg button.attachment')!);
    await until(() => $('.ui-dialog img'), 'the larger picture');
    const close = [...document.querySelectorAll<HTMLElement>('.ui-dialog button')].find((b) => b.textContent?.includes('Close'))!;
    expect(close).toBeTruthy();
    await click(close);
    await until(() => !$('.ui-dialog'), 'the dialog closed');
  });

  it('says in words when the image cannot be read, and when it is no longer kept', async () => {
    const s = setup();
    const [, b] = seed(s);
    s.daemon.uploads.expire(b!.id);
    const call = s.client.call.bind(s.client) as (m: string, p?: { id?: string }) => Promise<unknown>;
    (s.client as unknown as { call: typeof call }).call = (m, p) =>
      m === 'attachment.get' && p?.id !== b!.id ? Promise.reject(new Error('connection lost')) : call(m, p);
    await mount(s);
    await until(() => box.querySelector('.msg .attachment.failed'), 'the failure line');
    expect(box.querySelector('.msg .attachment.failed')!.textContent).toContain('could not be loaded');
    await until(() => box.querySelector('.msg .attachment.gone'), 'the gone line');
  });

  it('names a dropped image in words beside its caption', async () => {
    const s = setup();
    const a = s.daemon.uploads.addMemoFile('first.png', 'image/png', PNG);
    const image = { id: a.id, name: a.name, mime: 'image/png' as const, bytes: a.bytes };
    s.daemon.chat.seed(ARCH, { from: ARCH, to: 'human', body: 'see', attachments: [image], parts: [{ text: 'see ' }, { image, caption: 'one', gone: true }] });
    await mount(s);
    await until(() => box.querySelector('.msg .attachment.gone'), 'the dropped image');
    expect(box.querySelector('.msg .attachment.gone')!.textContent).toBe('one - no longer kept');
  });
});
