// The top bar: one on every page, one 44 px row by structure, the sidebar button while the sidebar is folded.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const click = (el: Element) => act(async () => void (el as HTMLElement).click());

/** The text grid keeps the terminal out of the way. */
async function mount(agent: string) {
  const data = new Map<string, string>([['terminal', 'text']]);
  const storage: KV = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
  const store = new Store(setup().client);
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={storage} />);
  });
}

beforeEach(() => {
  history.replaceState(null, '');
  vi.stubGlobal('confirm', () => true);
});
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  vi.unstubAllGlobals();
});

describe('the top bar', () => {
  const ROUTES = [
    '#/my-turn',
    '#/tasks',
    '#/requests',
    '#/stats',
    '#/settings',
    '#/team',
    '#/admin',
    `#/agents/${ARCH}`,
    `#/agents/${ARCH}/settings`,
  ];
  it('is there on every page, once, and a page head is drawn inside it, not in the page', async () => {
    await mount(ARCH);
    await until(() => $('.rail'), 'sidebar');
    for (const hash of ROUTES) {
      await act(async () => void (location.hash = hash));
      await wait(30);
      expect($$('.topbar'), hash).toHaveLength(1);
      expect($$('.main > section > header'), hash).toEqual([]);
    }
    await act(async () => void (location.hash = '#/my-turn'));
    await until(() => $('.topbar .page-bar header'), 'the My turn head in the bar');
  });
  it('keeps the bar one 44 px row by structure (jsdom has no layout; the height itself was measured in Chrome): no wrap on Tasks, dense tabs on Settings, a 32 px title field', async () => {
    await mount(ARCH);
    await until(() => $('.rail'), 'sidebar');
    await act(async () => void (location.hash = '#/tasks'));
    await until(() => $('.topbar .tasks-head'), 'the Tasks head in the bar');
    expect($('.topbar .tasks-head')!.className).toContain('flex-nowrap');
    await act(async () => void (location.hash = '#/settings'));
    await until(() => $('.topbar [role=tablist]'), 'the Settings tabs in the bar');
    expect($('.topbar [role=tablist]')!.className).not.toContain('py-2'); // the dense row, no padding of its own
    expect($('.topbar [role=tablist]')!.closest('.basis-full')).toBeNull(); // on the title's line, not under it
    await act(async () => void (location.hash = '#/tasks/1700'));
    await until(() => $('.topbar [aria-label="Edit Title"]'), 'the title in the bar');
    await click($('.topbar [aria-label="Edit Title"]')!);
    expect($('.topbar input[aria-label="Title"]')!.className).toContain('h-8');
    const input = $('.topbar input[aria-label="Title"]')!; // jsdom has no layout: the structure that kept the field visible at 820 and 1440
    expect(input.className).not.toMatch(/min-w-\[/);
    expect(input.closest('h2')!.className).not.toContain('truncate'); // a truncating heading turned the whole field into an ellipsis
    expect(input.closest('h2')!.className).toContain('flex-1'); // the title takes the bar's free width: a long saved title truncates on one row, the field fills the rest
  });
  it("gives the agent's name the room it needs, capped by a share of the bar and not a fixed 160 px", async () => {
    await mount(ARCH);
    await until(() => $('.topbar h2'), 'the agent name in the bar');
    expect($('.topbar h2')!.textContent).toBe(ARCH);
    expect($('.topbar h2')!.className).not.toContain('max-w-40'); // jsdom has no layout: the cap that cut the name at 1440 is gone
    expect($('.topbar h2')!.className).toContain('max-w-[45%]');
  });
  it('carries the sidebar button, Back and Forward while the sidebar is folded, and not while it is open', async () => {
    await mount(ARCH);
    await until(() => $('.rail'), 'sidebar');
    expect($('.topbar [aria-label="Show the agent list (⌘B)"]')).toBeNull();
    await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: 'b', metaKey: true })));
    for (const hash of ['#/tasks', `#/agents/${ARCH}`]) {
      await act(async () => void (location.hash = hash));
      expect(
        $$('.topbar [aria-label]').map((b) => b.getAttribute('aria-label')),
        hash,
      ).toEqual(expect.arrayContaining(['Show the agent list (⌘B)', 'Back (⌘[)', 'Forward (⌘])']));
    }
    expect($('.rail-handle')).toBeNull();
  });
});
