// Paste or drop an image into the body of a new task: it uploads and its reference lands at the cursor.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PNG = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), (c) =>
  c.charCodeAt(0),
);
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
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

async function openNewTask() {
  const s = setup();
  location.hash = '#/tasks';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  const btn = () => [...box.querySelectorAll('button')].find((b) => b.textContent?.trim().startsWith('New task'));
  await until(btn, 'the New task button');
  await act(async () => void btn()!.click());
  await until(() => document.querySelector('#newtask-body'), 'the body box');
  return document.querySelector<HTMLTextAreaElement>('#newtask-body')!;
}

const type = (el: HTMLTextAreaElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.setSelectionRange(5, 5);
  });

describe('images in a task body', () => {
  it('a dropped image is uploaded and its reference put at the cursor', async () => {
    const body = await openNewTask();
    await type(body, 'seeit more');
    const drop = new Event('drop', { bubbles: true, cancelable: true });
    Object.assign(drop, { dataTransfer: { types: ['Files'], files: [new File([PNG], 'shot.png', { type: 'image/png' })] } });
    await act(async () => void body.dispatchEvent(drop));
    await until(() => /attachment:/.test(body.value), 'the reference');
    expect(body.value).toMatch(/^seeit!\[shot\]\(attachment:[0-9A-Za-z-]+\) more$/);
    expect(drop.defaultPrevented).toBe(true);
  });

  it('a pasted text is left alone', async () => {
    const body = await openNewTask();
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.assign(paste, { clipboardData: { files: [] } });
    await act(async () => void body.dispatchEvent(paste));
    expect(paste.defaultPrevented).toBe(false);
  });
});
