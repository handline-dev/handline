// Comments on a task: the composer under the list; empty is not sent, a sent one joins the list and clears the box, a refusal keeps the text and says why.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
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
const section = () => box.querySelector<HTMLElement>('section[aria-label="Activity"]');
const send = () => [...section()!.querySelectorAll('button')].find((b) => b.textContent === 'Comment') as HTMLButtonElement;
const typeInto = async (value: string) => {
  await act(async () => void [...section()!.querySelectorAll('button')].find((b) => b.textContent === 'Source')!.click());
  const el = section()!.querySelector('textarea')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
};

async function mount() {
  const s = setup();
  s.daemon.tasks.verbs = true;
  location.hash = '#/tasks/1688';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(section, 'the comments');
  await until(() => send(), 'the composer');
  return s;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('Comments on a task', () => {
  it('does not send an empty comment', async () => {
    await mount();
    expect(send().disabled).toBe(true);
    await typeInto('   ');
    expect(send().disabled).toBe(true);
  });

  it('sends the comment, lists it with its author and clears the box', async () => {
    const s = await mount();
    await typeInto('why this way? @handline.architect');
    await act(async () => void send().click());
    await until(() => s.daemon.tasks.get({ task: 1688 }).memos.some((m: { text: string }) => m.text.includes('why this way?')), 'the daemon has it');
    await until(() => section()!.querySelector('.comment')?.textContent?.includes('why this way?'), 'the comment in the list');
    await until(() => !section()!.querySelector('textarea'), 'the box to clear');
    expect(box.querySelector('[aria-label="Message about this task"]')).toBeNull(); // no separate reply box
  });

  it('keeps the text and says why when the daemon refuses', async () => {
    const s = await mount();
    s.daemon.knobs.refuse('task.memo.add', 'state', 'the task is closed', undefined, 1);
    await typeInto('still there?');
    await act(async () => void send().click());
    await until(() => section()!.textContent!.includes('the task is closed'), 'the refusal');
    expect(section()!.querySelector('textarea')!.value).toBe('still there?');
  });

  it('Escape in an inline field leaves it as it was and saves nothing', async () => {
    const s = await mount();
    const before = s.daemon.tasks.get({ task: 1688 }).title;
    await act(async () => void box.querySelector<HTMLElement>('[aria-label="Edit Title"]')!.click());
    const field = box.querySelector<HTMLInputElement>('input[aria-label="Title"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, 'Never saved');
      field.dispatchEvent(new Event('input', { bubbles: true }));
      field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(box.querySelector('input[aria-label="Title"]')).toBeNull();
    await wait(50);
    expect(s.daemon.tasks.get({ task: 1688 }).title).toBe(before);
  });
});
