// A task number in a chat message is a link that opens the task: plain click in the Tasks tab, ctrl or middle click in a tab of its own.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { second, setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
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
const links = () => [...box.querySelectorAll<HTMLElement>('.msg button[data-task]')].map((b) => b.dataset.task);
const link = (id: string) => box.querySelector<HTMLElement>(`.msg button[data-task="${id}"]`)!;
const tabs = () => [...box.querySelectorAll<HTMLElement>('[role=tab]')].map((t) => t.dataset.tab);

async function mount() {
  const s = setup();
  const c = second(s.daemon, s.net, { client: 'web-other' });
  await c.start();
  await c.call('chat.send', { agent: ARCH, body: 'look at T1684, not T99999 or T1684x' });
  c.close();
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => links().length, 'the task link');
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('task numbers in a message', () => {
  it('link only a task the daemon has', async () => {
    await mount();
    expect(links()).toEqual(['1684']);
    expect(box.textContent).toContain('T99999');
  });

  it('open in the Tasks tab on a click, in a tab of their own on ctrl-click', async () => {
    await mount();
    await act(async () => link('1684').click());
    await until(() => box.querySelector('.tasks-tab, .tbody'), 'the task');
    expect(tabs()).toContain('tasks');
    await act(async () => box.querySelector<HTMLElement>('[data-tab="chat"]')!.click());
    await act(async () => link('1684').dispatchEvent(new MouseEvent('click', { bubbles: true, ctrlKey: true })));
    await until(() => tabs().includes('tasks:1684'), 'the tab of its own');
    expect(box.querySelector('[data-tab="tasks:1684"]')!.textContent).toContain('T1684');
  });

  it('a middle click opens a tab of its own too', async () => {
    await mount();
    await act(async () => link('1684').dispatchEvent(new MouseEvent('auxclick', { bubbles: true, button: 1 })));
    await until(() => tabs().includes('tasks:1684'), 'the tab of its own');
  });
});
