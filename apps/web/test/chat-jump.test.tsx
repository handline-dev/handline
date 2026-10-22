// A jump to an old message: the window replaces the tail at once, and stays where the jump put it until the person scrolls.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

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
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const msg = (body: string) => [...box.querySelectorAll<HTMLElement>('.msg')].find((m) => m.textContent?.includes(body));
const rows = () => box.querySelectorAll('.stream .msg[data-id]').length;

/** The chat with the plan message far below the newest page; it is found by search and the window is open. */
async function jumped() {
  const s = setup();
  for (let i = 0; i < 120; i++)
    s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: `filler ${i}`, at: new Date(Date.UTC(2031, 0, 1, 0, i)).toISOString() });
  s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: 'newest message', at: '2032-01-01T00:00:00Z' });
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => $('.chat-head'), 'the chat head');
  expect($('.chat-search')).toBeNull(); // no permanent box
  await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: 'f', metaKey: true })));
  await until(() => $('.chat-search input'), 'the find bar');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call($('.chat-search input'), 'chat tab plan');
    $('.chat-search input')!.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await until(() => $('.chat-search .hit'), 'a hit');
  await act(async () => $('.chat-search .hit')!.click());
  await until(() => $('.latest') && msg('Draft the web chat tab plan.'), 'the window');
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
  vi.unstubAllGlobals();
});

describe('a jump to an old message', () => {
  it('opens a window with none of the tail rows in the list', async () => {
    await jumped();
    expect(msg('newest message')).toBeUndefined();
    expect(msg('filler 119')).toBeUndefined();
  });

  it('keeps the window as it landed until the person scrolls', async () => {
    const live = new Set<{ cb: (e: { isIntersecting: boolean }[]) => void }>();
    class Watch {
      cb: (e: { isIntersecting: boolean }[]) => void;
      constructor(cb: (e: { isIntersecting: boolean }[]) => void) {
        this.cb = cb;
      }
      observe() {
        live.add(this);
      }
      unobserve() {}
      disconnect() {
        live.delete(this);
      }
    }
    vi.stubGlobal('IntersectionObserver', Watch);
    await jumped();
    const before = rows();
    const fire = () => act(async () => live.forEach((w) => w.cb([{ isIntersecting: true }])));
    await fire(); // the jump leaves the edges of the window near the box
    await wait(300);
    expect(rows()).toBe(before);
    await act(async () => void $('.stream')!.dispatchEvent(new Event('wheel', { bubbles: true })));
    await fire();
    await until(() => rows() > before, 'a page after the person scrolls');
  });
});
