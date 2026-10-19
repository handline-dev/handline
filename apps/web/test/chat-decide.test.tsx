// A message tied to a task shows the daemon's decision buttons; a tap runs the task verb, and Send back asks for a note first.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const ME = 'web-test';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
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
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const click = (el: Element) => act(async () => void (el as HTMLElement).click());

function seeded(task: number) {
  const s = setup();
  s.daemon.tasks.verbs = true;
  s.daemon.tasks.seed({
    id: task,
    from: ME,
    to: ARCH,
    title: `Report ${task}`,
    state: 'review',
    report: { by: ARCH, evidence: 'built', at: new Date().toISOString(), waits_on: ME, accepted: null },
  });
  (s.daemon as unknown as { chat: { seed: (a: string, m: object) => number } }).chat.seed(ARCH, {
    body: `T${task} is reported`,
    from: ARCH,
    to: ME,
    tasks: [task],
    actions: [
      { key: 'accept', label: 'Accept', verb: 'task.accept', task, params: { task } },
      { key: 'send_back', label: 'Send back', verb: 'task.reject', task, params: { task }, needs: 'note' },
    ],
  });
  return s;
}

async function mount(s: ReturnType<typeof setup>) {
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => $('.msg-actions'), 'the buttons');
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('decide buttons on a chat message', () => {
  it('Accept calls task.accept', async () => {
    const s = seeded(9101);
    await mount(s);
    await click($('[data-action="accept"]')!);
    await until(() => s.daemon.tasks.get({ task: 9101 }).state === 'done', 'the daemon');
    await until(() => !$('.msg-actions'), 'the buttons gone once the task left review');
  });

  it('Send back asks for a note, then calls task.reject with it', async () => {
    const s = seeded(9102);
    await mount(s);
    await click($('[data-action="send_back"]')!);
    expect(s.daemon.tasks.get({ task: 9102 }).state).toBe('review');
    const field = $('.msg-actions input')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, 'Tests missing');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click([...box.querySelectorAll<HTMLElement>('.msg-actions button')].find((b) => b.textContent === 'Send back' && b.closest('form'))!);
    await until(() => s.daemon.tasks.get({ task: 9102 }).state === 'queued', 'the daemon');
    await until(() => !$('.msg-actions'), 'the buttons gone once the task was sent back');
  });
});
