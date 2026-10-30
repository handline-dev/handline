// The photo viewer: the scrim closes it, M starts marking, drag draws a numbered mark with a note, one button sends the notes as ONE message, and
// leaving with unsent marks asks first; against the fake daemon.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

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

const drag = async (el: Element, from: [number, number], to: [number, number]) => {
  const ev = (type: string, [x, y]: [number, number]) => el.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y }));
  await act(async () => void ev('pointerdown', from));
  await act(async () => void ev('pointermove', to));
  await act(async () => void ev('pointerup', to));
};
const button = (name: string) =>
  [...document.querySelectorAll<HTMLElement>('.image-viewer button')].find((b) => b.textContent?.includes(name));
const type = (el: HTMLTextAreaElement, text: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, text);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

async function open(s: ReturnType<typeof setup>) {
  seed(s);
  await mount(s);
  await until(() => box.querySelector('.msg .attachment img'), 'the pictures');
  await click(box.querySelector('.msg button.attachment')!);
  await until(() => $('.image-viewer img'), 'the viewer');
}

describe('the photo viewer', () => {
  it('closes on a click on the scrim, not on the photo', async () => {
    const s = setup();
    await open(s);
    await click($('.image-viewer [data-photo]')!);
    expect($('.image-viewer')).not.toBeNull();
    await click($('.image-viewer .stage')!);
    await until(() => !$('.image-viewer'), 'the viewer closed');
  });

  it('offers Copy photo and Save photo under More', async () => {
    const s = setup();
    await open(s);
    await click($('.image-viewer [aria-label="More"]')!);
    expect([...document.querySelectorAll('.image-viewer [role="menuitem"]')].map((m) => m.textContent)).toEqual([
      'Copy photo',
      'Save photo',
    ]);
  });

  it('marks, takes a note, sends ONE message with the numbered note, and asks before discarding unsent marks', async () => {
    const s = setup();
    await open(s);
    expect(button('Send')).toBeUndefined();
    await act(async () => void document.dispatchEvent(new KeyboardEvent('keydown', { key: 'm', bubbles: true })));
    await until(() => $('.image-viewer')!.textContent!.includes('Drag on the photo to mark'), 'the hint');
    await drag($('.image-viewer .stage')!.querySelector('[data-photo] img')!, [10, 10], [60, 50]);
    await until(() => $('.image-viewer textarea'), 'the note field');
    await type($('.image-viewer textarea') as HTMLTextAreaElement, 'Make it lighter');
    await act(async () => void (button('Done') as HTMLElement).click());
    await until(() => button('Send 1 note to ' + ARCH), 'the send button');
    expect($('.image-viewer .stage')!.textContent).toContain('1 Make it lighter');
    // Cancel with an unsent mark asks; keeping goes back to marking.
    await click(button('Cancel')!);
    expect($('.image-viewer')!.textContent).toContain('Discard 1 note?');
    await click(button('Keep marking')!);
    await click(button('Send 1 note to ' + ARCH)!);
    await until(() => !$('.image-viewer'), 'the viewer closed after sending');
    await until(
      () =>
        [...box.querySelectorAll('.msg')].some(
          (m) => m.textContent?.includes('One change on first.png') && m.textContent.includes('Make it lighter'),
        ),
      'the message with the note',
    );
  });
});
