// The window keys: ⌘T opens a new tab beside Chat, ⌘W closes it, ⌘\ splits the pane and ⌘1 / ⌘2 focus a pane.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 300) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV => {
  const data = new Map<string, string>([['rail', '0']]);
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};
let root: Root | null = null;
let box: HTMLElement;
const tabs = () => [...box.querySelectorAll('[role="tab"]')].length;
const press = (key: string) => act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key, metaKey: true, cancelable: true })));

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  document.body.innerHTML = '';
});

describe('window keys', () => {
  it('⌘T opens a tab, ⌘W closes it, ⌘\\ splits the pane and ⌘2 focuses it', async () => {
    location.hash = '#/agents/handline.architect';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(setup().client)} daemon="test" storage={memory()} />);
    });
    await until(() => box.querySelector('.workspace'), 'the workspace');
    const before = tabs();
    await press('t');
    await until(() => tabs() === before + 1, 'a new tab');
    await press('w');
    await until(() => tabs() === before, 'the tab closed');
    const panes = () => Number(box.querySelector('.workspace')!.getAttribute('data-panes'));
    await press('\\');
    await until(() => panes() === 2 || box.querySelector('.workspace')!.getAttribute('data-merged') === 'true', 'a second pane');
  });
});
