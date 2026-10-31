// The toast stack above the bell lifts above a chat's composer, also one that opens while a toast is up.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
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
let store: Store;
const toast = () => $('.toasts');

async function mount(agent: string, s = setup(), hash = `#/agents/${agent}`) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    store = new Store(s.client);
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
}
afterEach(async () => {
  vi.restoreAllMocks();
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});
describe('the toast stack', () => {
  const rect = (top: number, bottom: number, left: number, right: number) =>
    ({ top, bottom, left, right, width: right - left, height: bottom - top, x: left, y: top }) as DOMRect;
  const layout = () =>
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      if (this.classList.contains('compose-wrap')) return rect(700, 780, 300, 1380);
      if (this.classList.contains('toasts')) return rect(0, 100, 1000, 1380);
      return this.querySelector?.('.toasts') ? rect(860, 880, 1000, 1400) : rect(0, 0, 0, 0);
    });

  it('keeps the stack where it is when no composer is under it', async () => {
    layout();
    const s = setup();
    await mount(ARCH, s, '#/tasks');
    await act(async () => store.say('Saved'));
    await until(toast, 'the toast');
    await act(async () => void (await new Promise((r) => requestAnimationFrame(r)))); // a frame: the observers have measured
    expect(toast()!.style.marginBottom).toBe('');
  });

  it("lifts the stack above a chat's composer, also one that opens while a toast is up", async () => {
    layout();
    const s = setup();
    await mount(ARCH, s, '#/tasks');
    await act(async () => store.say('Saved'));
    await until(toast, 'the toast');
    expect(toast()!.style.marginBottom).toBe('');
    const late = document.body.appendChild(document.createElement('div'));
    late.className = 'compose-wrap';
    // the bar is at 860 and the composer starts at 700: the stack's foot is 8px above the composer
    await until(() => toast()!.style.marginBottom === '168px', 'the lift');
    late.remove();
  });

  it("paints a toast over the ones above it, so a toast's shadow never falls on the toast below", async () => {
    const s = setup();
    await mount(ARCH, s, '#/tasks');
    await act(async () => {
      store.say('First');
      store.say('Second');
    });
    await until(() => box.querySelectorAll('.toasts .toast').length === 2, 'two toasts');
    const z = [...box.querySelectorAll<HTMLElement>('.toasts .toast')].map((t) => Number(t.style.zIndex));
    expect(z[0]).toBeGreaterThan(z[1]!); // the first is the bottom one (the stack runs in reverse)
  });
});
