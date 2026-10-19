// A jump to an old message: the window replaces the tail at once, and stays where the jump put it until the person scrolls.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
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
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const msg = (body: string) => [...box.querySelectorAll<HTMLElement>('.msg')].find((m) => m.textContent?.includes(body));
const ids: number[] = [];
const rows = () => box.querySelectorAll('.stream .msg[data-id]').length;

/** The chat with the plan message far below the newest page, and a reference to it in the newest message; the reference is clicked and the window is open. */
async function jumped() {
  const s = setup();
  for (let i = 0; i < 120; i++)
    ids.push(
      s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: `filler ${i}`, at: new Date(Date.UTC(2031, 0, 1, 0, i)).toISOString() }),
    );
  s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: 'see #100 for the plan', at: '2032-01-01T00:00:00Z' }); // local-ref-ok
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => $('.ref'), 'the reference on the newest page');
  await act(async () => $('.ref')!.click());
  await until(() => $('.latest') && msg('Draft the web chat tab plan.'), 'the window');
}

afterEach(async () => {
  ids.length = 0;
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
  vi.unstubAllGlobals();
});

describe('a jump to an old message', () => {
  it('opens a window with none of the tail rows in the list', async () => {
    await jumped();
    expect(msg('see #100')).toBeUndefined(); // local-ref-ok
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

  it('a second jump to a message the window does not hold relocates to it', async () => {
    await jumped();
    const far = ids[119]!; // the newest filler: not in the window around #100
    expect(msg('filler 119')).toBeUndefined();
    await act(async () => void (location.hash = `#/agents/${ARCH}?m=${far}`));
    await until(() => msg('filler 119'), 'the window of the second target');
  });
});
