// The weekly-loss notice: a task for the person tagged spare-notice is a card above the bell, with the first three spare tasks and a
// Start button; Start and the X answer the task.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { applySeeds } from '@handline/fake-daemon/seeds';
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
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const open = (s: ReturnType<typeof setup>) => {
  s.daemon.tasks.verbs = true;
  applySeeds(s.daemon, 'spare-notice');
};
const spare = () => $('.notice-spare');

async function mount(agent: string, s = setup(), hash = `#/agents/${agent}`) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
}
afterEach(async () => {
  vi.restoreAllMocks();
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});
const button = (word: string) =>
  [...(spare()?.querySelectorAll('button') ?? [])].find((b) => text(b).startsWith(word) || b.getAttribute('aria-label') === word);

describe('the weekly-loss notice', () => {
  it('shows the sentence, three of the four spare tasks and a button that says how many it starts', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await until(spare, 'the notice');
    expect(text(spare())).toContain("18% of theta's weekly use will be lost at 02:10. These spare tasks can use it:");
    expect(text(spare())).toContain('Tidy the gap list');
    expect(text(spare())).not.toContain('T7104');
    expect(text(spare())).toContain('and 1 more');
    expect(text(spare())).toContain('architect');
    expect(text(spare())).not.toContain('handline.architect');
    expect(button('Start 4 tasks')).toBeDefined();
  });

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
    open(s);
    await mount(ARCH, s, '#/tasks');
    await until(spare, 'the notice');
    await act(async () => void (await new Promise((r) => requestAnimationFrame(r)))); // a frame: the observers have measured
    expect($('.toasts')!.style.marginBottom).toBe('');
  });

  it("lifts the stack above a chat's composer, also one that opens while the card is up", async () => {
    layout();
    const s = setup();
    open(s);
    await mount(ARCH, s, '#/tasks');
    await until(spare, 'the notice');
    expect($('.toasts')!.style.marginBottom).toBe('');
    const late = document.body.appendChild(document.createElement('div'));
    late.className = 'compose-wrap';
    // the bar is at 860 and the composer starts at 700: the stack's foot is 8px above the composer
    await until(() => $('.toasts')!.style.marginBottom === '168px', 'the lift');
    late.remove();
  });

  it('Start answers the task and the notice goes; the X goes without starting', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await until(spare, 'the notice');
    await click(button('Start 4 tasks')!);
    await until(() => !spare(), 'the notice gone');
  });

  it('the X closes it', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await until(spare, 'the notice');
    await click(button('Close this notice')!);
    await until(() => !spare(), 'the notice gone');
  });
});
