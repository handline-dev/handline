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

async function mount(hash: string) {
  const s = setup();
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  return s;
}
const button = (label: string) => [...document.querySelectorAll('button')].find((b) => b.textContent?.trim().startsWith(label));

async function openNewTask() {
  const s = await mount('#/tasks');
  await until(() => button('New task'), 'the New task button');
  await act(async () => void button('New task')!.click());
  await until(() => document.querySelector('#newtask-body'), 'the body box');
  return { s, body: document.querySelector<HTMLTextAreaElement>('#newtask-body')! };
}

const type = (el: HTMLTextAreaElement, value: string, at = value.length) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.setSelectionRange(at, at);
  });
const png = (name: string, type = 'image/png') => new File([PNG], name, { type });
const drop = (el: HTMLElement, files: File[]) => {
  const e = new Event('drop', { bubbles: true, cancelable: true });
  Object.assign(e, { dataTransfer: { types: ['Files'], files } });
  return act(async () => void el.dispatchEvent(e));
};

describe('images in a task body', () => {
  it('a dropped image is uploaded and its reference put at the cursor', async () => {
    const { body } = await openNewTask();
    await type(body, 'seeit more', 5);
    await drop(body, [png('shot.png')]);
    await until(() => /attachment:/.test(body.value), 'the reference');
    expect(body.value).toMatch(/^seeit!\[shot\]\(attachment:[0-9A-Za-z-]+\) more$/);
  });

  it('several images land in file order, and a refused one is skipped with its reason shown', async () => {
    const { body } = await openNewTask();
    await drop(body, [png('first.png'), png('notes.svg', 'image/svg+xml'), png('second.png')]);
    await until(() => /second/.test(body.value), 'the references');
    expect(body.value).toMatch(/^!\[first\]\(attachment:[0-9A-Za-z-]+\)\n!\[second\]\(attachment:[0-9A-Za-z-]+\)$/);
    expect(document.querySelector('.ui-note')?.textContent).toMatch(/svg|image|type/i);
  });

  it('the reference goes where the cursor is when the upload ends, and Ask waits for it', async () => {
    const { s, body } = await openNewTask();
    await type(body, 'abc', 3);
    const release = s.daemon.knobs.hold('attachment.put');
    await drop(body, [png('late.png')]);
    expect((button('Ask') as HTMLButtonElement).disabled).toBe(true);
    await type(body, 'abc def', 4);
    release();
    await until(() => /attachment:/.test(body.value), 'the reference');
    expect(body.value).toMatch(/^abc !\[late\]\(attachment:[0-9A-Za-z-]+\)def$/);
    await until(() => !(button('Ask') as HTMLButtonElement).disabled, 'Ask to wake');
  });

  it('a dropped image in the edit sheet is saved with the task', async () => {
    const s = await mount('#/tasks/1700');
    await until(() => document.querySelector('.tverbs'), 'the verbs');
    await act(async () => void button('Edit')!.click());
    const form = [...document.querySelectorAll('.tsheet')].find((x) => x.getAttribute('aria-label') === 'Edit T1700')!; // local-ref-ok
    const body = form.querySelector('textarea')!;
    await drop(body, [png('shot.png')]);
    await until(() => /attachment:/.test(body.value), 'the reference');
    await act(async () => void (form.querySelector('button[type="submit"]') as HTMLButtonElement).click());
    await until(() => /attachment:/.test(s.daemon.tasks.get({ task: 1700 }).body ?? ''), 'the daemon to hold the reference');
  });

  it('a pasted text is left alone', async () => {
    const { body } = await openNewTask();
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.assign(paste, { clipboardData: { files: [] } });
    await act(async () => void body.dispatchEvent(paste));
    expect(paste.defaultPrevented).toBe(false);
  });
});
