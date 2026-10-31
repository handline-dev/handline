// The tabs in the top bar: what does not fit goes to a "more" menu, and closing a pane keeps its tabs.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, closeTab, defaultWorkspace, openTab, saveWorkspace, splitTab, storageKey, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
let box: HTMLElement;
let root: Root | null = null;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const text = (el: Element | null) => el?.textContent ?? '';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const click = (el: Element) => act(async () => void (el as HTMLElement).click());

/** The storage holds the layout; the text grid keeps the terminal out of the way. */
function storageWith(ws: ReturnType<typeof defaultWorkspace>): KV {
  const data = new Map<string, string>([['terminal', 'text']]);
  const kv: KV = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
  saveWorkspace(kv, storageKey('test', ARCH), ws);
  return kv;
}
async function mount(storage: KV) {
  const store = new Store(setup().client);
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={storage} />);
  });
}
const restores: (() => void)[] = [];
beforeEach(() => history.replaceState(null, '')); // the layout rides in the history entry: each test starts clean
afterEach(async () => {
  restores.splice(0).forEach((r) => r());
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  vi.unstubAllGlobals();
});

describe('the tabs in the top bar', () => {
  it('tabs that do not fit go to a "more" menu, the showing tab stays in the run, and a picked one comes in', async () => {
    let ws = closeTab(defaultWorkspace({ terminal: `term:${ARCH}` }), 'activity');
    ws = openTab(openTab(openTab(ws, 'markdown', { path: 'a.md' }), 'web', { port: 3000 }), 'tasks'); // the last one opened is showing
    vi.stubGlobal(
      'ResizeObserver',
      class {
        cb: (entries: unknown[]) => void;
        constructor(cb: (entries: unknown[]) => void) {
          this.cb = cb;
        }
        observe() {
          this.cb([]);
        }
        disconnect() {}
      },
    );
    const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('strip') ? 300 : 0; // room for two 96 px tabs and the menu button
    });
    restores.push(() => width.mockRestore());
    await mount(storageWith(ws));
    await until(() => $('.topbar .more-tabs'), 'the more menu');
    const shown = () => $$('.topbar [role=tab]:not(.hidden)').map((t) => t.getAttribute('data-tab'));
    expect(shown()).toHaveLength(2);
    expect(shown()).toContain('tasks'); // the tab showing is always in the run
    await click($('.topbar .more-tabs')!);
    await click([...document.querySelectorAll<HTMLElement>('[role=menuitem]')].find((b) => text(b).includes('markdown'))!);
    expect(shown().some((id) => id?.startsWith('markdown'))).toBe(true); // picking it shows it, and it takes a place in the run
  });

});
