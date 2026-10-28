// The founder's state verbs on the task page act at once, with no box; anyone else is still asked.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 400; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const press = (key: string, el: Element) =>
  act(async () => void el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })));
const sheet = (label: string) => [...box.querySelectorAll<HTMLElement>('.tsheet')].find((s) => s.getAttribute('aria-label') === label);

async function mount(s: ReturnType<typeof setup>) {
  s.daemon.tasks.verbs = true;
  location.hash = '#/tasks/1688';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  await until(() => $('.tverbs'), 'the verbs');
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

it('releases and discards at once for the founder, with no box, and the daemon writes its own word', async () => {
  const s = setup();
  await mount(s);
  await press('l', $('.tbody')!); // doing: release
  await until(() => s.daemon.tasks.get({ task: 1688 }).state === 'queued', 'released');
  expect($('.tsheet')).toBeNull();
  await press('d', $('.tbody')!);
  await until(() => s.daemon.tasks.get({ task: 1688 }).state === 'discarded', 'discarded');
  expect($('.tsheet')).toBeNull();
  expect(s.daemon.tasks.get({ task: 1688 }).hold_reason).toBe('Discarded');
});

it('still asks anyone but the founder before it changes a state', async () => {
  const s = setup({ role: 'agent' });
  await mount(s);
  await press('d', $('.tbody')!);
  await until(() => sheet('stop it and discard - why:'), 'the discard sheet');
  expect(s.daemon.tasks.get({ task: 1688 }).state).not.toBe('discarded');
});
