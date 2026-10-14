// The ⌘K palette: every page is a hit, a box of two characters adds the messages that hold it, and a message hit lands the chat on it.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 400; i++) {
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
const $$ = (sel: string) => [...document.querySelectorAll<HTMLElement>(sel)];
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const options = () => $$('.palette [role="option"]').map((o) => text(o));

async function mount(daemon: { chat?: boolean } = {}) {
  const store = new Store(setup({ daemon }).client);
  location.hash = '#/inbox';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
  await until(() => $('.rail'), 'the sidebar');
  await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true })));
}
const type = async (value: string) => {
  const input = $$('.palette input')[0] as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await wait(300); // the box waits for the typing to stop
};

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  document.body.innerHTML = '';
});

describe('palette', () => {
  it('lists every page, Worktrees, New agent and HR among them, and goes to one', async () => {
    await mount();
    for (const page of ['Inbox', 'Tasks', 'Team', 'Worktrees', 'New agent', 'HR', 'Accounts', 'Stats', 'Settings'])
      expect(options().some((o) => o.includes(page))).toBe(true);
    await type('worktrees');
    await act(async () => void $$('.palette [role="option"] button')[0]!.click());
    expect(location.hash).toBe('#/worktrees');
  });

  it('adds a Messages group for two characters and lands the chat on the message of a hit', async () => {
    await mount();
    await type('contract');
    await until(() => options().some((o) => o.includes('Use the contract fixtures.')), 'a message hit');
    const hit = $$('.palette [role="option"] button').find((b) => text(b).includes('Use the contract fixtures.'))!;
    expect(text(hit)).toContain('handline.architect');
    await act(async () => void hit.click());
    expect(location.hash).toBe('#/agents/handline.architect');
    await until(() => $$('.msg[data-flash]').some((m) => text(m).includes('Use the contract fixtures.')), 'the flashed message');
  });

  it('adds no Messages group for one character', async () => {
    await mount();
    await type('c');
    await wait(50);
    expect(options().some((o) => o.includes('Use the contract fixtures.'))).toBe(false);
  });

  it('shows no Messages group on a daemon that does not serve chat.search', async () => {
    await mount({ chat: false });
    await type('contract');
    await wait(50);
    expect(options().some((o) => o.includes('Use the contract fixtures.'))).toBe(false);
  });
});
