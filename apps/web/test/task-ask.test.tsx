// Message about a task: a question to the holder from the task detail; empty is not sent, a refusal keeps the text and says why.
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
const section = () => box.querySelector<HTMLElement>('section[aria-label="Message about this task"]');
const field = () => section()!.querySelector<HTMLTextAreaElement>('textarea')!;
const submit = () => section()!.querySelector<HTMLButtonElement>('button[type=submit]')!;
const typeInto = (el: HTMLTextAreaElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

async function mount() {
  const s = setup();
  location.hash = '#/tasks/1688';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(section, 'the ask box');
  return s;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('Message about a task', () => {
  it('does not send an empty question', async () => {
    const s = await mount();
    const holder = () => section()!.querySelector('button[aria-pressed=true]')!.textContent!;
    const count = async () => (await s.client.call('chat.messages', { agent: holder(), scope: 'all', limit: 50 })).messages.length;
    const before = await count();
    await typeInto(field(), '   ');
    expect(submit().disabled).toBe(true);
    await act(async () => section()!.querySelector('form')!.requestSubmit());
    await wait(50);
    expect(await count()).toBe(before);
  });

  it('sends the question to the holder, linked to the task, and clears the box', async () => {
    const s = await mount();
    await typeInto(field(), 'why this way?');
    await act(async () => void submit().click());
    await until(() => field().value === '', 'the box to clear');
    const to = section()!.querySelector('button[aria-pressed=true]')!.textContent!;
    const page = await s.client.call('chat.messages', { agent: to, scope: 'all', limit: 5 });
    const q = page.messages.find((m) => m.body.includes('why this way?'))!;
    expect(q.tasks.map((t) => t.id)).toEqual([1688]);
  });

  it('keeps the text and says why when the daemon refuses', async () => {
    const s = await mount();
    s.daemon.knobs.refuse('task.ask', 'state', 'the holder is gone', undefined, 1);
    await typeInto(field(), 'still there?');
    await act(async () => void submit().click());
    await until(() => section()!.textContent!.includes('the holder is gone'), 'the refusal');
    expect(field().value).toBe('still there?');
  });
});
