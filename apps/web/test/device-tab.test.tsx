// The Android and iOS tabs in the desktop shell: the device's picture, a click on the Android one is a tap, a plain sentence when there is no device, Mark an area to ask.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const items = () => [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')];
const button = (label: string) => $$('button').find((b) => (b.textContent ?? '').trim().startsWith(label));
const typeInto = (el: HTMLTextAreaElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });



const taps: { x: number; y: number }[] = [];
let reply: { ok: true; png: string } | { ok: false; code: 'unavailable'; message: string };
async function mount(hash: string) {
  const s = setup();
  Object.assign(URL, { createObjectURL: () => 'blob:test', revokeObjectURL: () => undefined });
  Object.assign(window, {
    handline: {
      api: 1,
      screen: async () => ({ ok: true }),
      unsent: async () => ({ ok: true }),
      deviceShot: async () => reply,
      deviceTap: async (a: { x: number; y: number }) => (taps.push(a), { ok: true }),
    },
  });
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  delete (window as { handline?: unknown }).handline;
  location.hash = '';
  taps.length = 0;
});

async function openTab(name: RegExp) {
  await until(() => $('[aria-label="Add a tab"]'), 'the + menu');
  await click($('[aria-label="Add a tab"]'));
  await until(() => items().find((i) => name.test(i.textContent ?? '')), 'the menu item');
  await click(items().find((i) => name.test(i.textContent ?? '')));
}

describe('the Android and iOS tabs', () => {
  it('shows the device and sends a click as a tap', async () => {
    reply = { ok: true, png: PNG };
    await mount('#/agents/handline.architect');
    await openTab(/Android/);
    await until(() => $('img[alt="Android screen"]'), 'the picture');
    const img = $('img[alt="Android screen"]')!;
    img.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 400, right: 200, bottom: 400, x: 0, y: 0, toJSON: () => ({}) });
    await act(async () => void img.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 100, clientY: 100 })));
    expect(taps).toEqual([{ x: 0.5, y: 0.25 }]);
  });

  it('says plainly when no device is running', async () => {
    reply = { ok: false, code: 'unavailable', message: 'No iOS simulator is booted. Boot one and it shows here.' };
    await mount('#/agents/handline.architect');
    await openTab(/iOS/);
    await until(() => $('[role="status"]'), 'the sentence');
    expect($('[role="status"]')!.textContent).toContain('No iOS simulator is booted');
  });

  it('sends the marked picture to the agent chat', async () => {
    reply = { ok: true, png: PNG };
    const s = await mount('#/agents/handline.architect');
    await openTab(/iOS/);
    await until(() => button('Mark an area'), 'the Mark button');
    await click(button('Mark an area'));
    await until(() => $('[data-testid="capture-preview"]'), 'the picture');
    await typeInto($('textarea[aria-label="Question"]') as HTMLTextAreaElement, 'Why is this blank');
    await click([...document.querySelectorAll<HTMLElement>('.ui-dialog button')].find((b) => (b.textContent ?? '').trim().startsWith('Send')));
    await until(() => !$('[data-testid="capture-preview"]'), 'the dialog to close', 600);
    const r = (await s.client.call('chat.messages', { agent: 'handline.architect', scope: 'all' } as never)) as { messages: { body: string }[] };
    expect(r.messages.some((m) => m.body.includes('Why is this blank'))).toBe(true);
  });
});
