// The Preview tab in the desktop shell: Mark an area sends the preview's picture with a question to the agent's chat; Open outside opens the address in the browser.
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


const opened: string[] = [];
async function mount(hash: string) {
  const s = setup();
  Object.assign(window, {
    handline: {
      api: 1,
      screen: async () => ({ ok: true }),
      unsent: async () => ({ ok: true }),
      preview: async () => ({ ok: true }),
      capturePreview: async () => ({ ok: true, png: PNG }),
    },
  });
  window.open = ((u: string) => void opened.push(u)) as unknown as typeof window.open;
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
});

async function openPreview(url: string) {
  await until(() => $('[aria-label="Add a tab"]'), 'the + menu');
  await click($('[aria-label="Add a tab"]'));
  await until(() => items().find((i) => /Preview/.test(i.textContent ?? '')), 'the Preview item');
  await click(items().find((i) => /Preview/.test(i.textContent ?? '')));
  await until(() => $('input[aria-label="Address"]'), 'the address field');
  const input = $('input[aria-label="Address"]') as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, url);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => void (input.form as HTMLFormElement).requestSubmit());
}

describe('the Preview tab', () => {
  it('opens the address outside the app', async () => {
    await mount('#/agents/handline.architect');
    await openPreview('localhost:5173');
    await until(() => button('Open outside'), 'the Open outside menu');
    await click(button('Open outside'));
    await until(() => items().find((i) => /browser/.test(i.textContent ?? '')), 'the item');
    await click(items().find((i) => /browser/.test(i.textContent ?? '')));
    expect(opened).toEqual(['http://localhost:5173']);
  });

  it('sends the picture with the question to the agent chat', async () => {
    const s = await mount('#/agents/handline.architect');
    await openPreview('localhost:5173');
    await until(() => button('Mark an area'), 'the Mark button');
    await click(button('Mark an area'));
    await until(() => $('[data-testid="capture-preview"]'), 'the picture');
    await typeInto($('textarea[aria-label="Question"]') as HTMLTextAreaElement, 'Make this button blue');
    await click([...document.querySelectorAll<HTMLElement>('.ui-dialog button')].find((b) => (b.textContent ?? '').trim().startsWith('Send')));
    await until(() => !$('[data-testid="capture-preview"]'), 'the dialog to close', 600);
    const r = (await s.client.call('chat.messages', { agent: 'handline.architect', scope: 'all' } as never)) as { messages: { body: string; attachments?: unknown[] }[] };
    const sent = r.messages.find((m) => m.body.includes('Make this button blue'));
    expect(sent).toBeTruthy();
    expect(sent!.attachments).toHaveLength(1);
  });
});
