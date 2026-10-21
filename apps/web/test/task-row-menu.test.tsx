// The Tasks page row menu: ⋯ on hover, right-click, one open at a time.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV & { data: Map<string, string> } => {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string, from: ParentNode = box) => from.querySelector<HTMLElement>(sel);
const $$ = (sel: string, from: ParentNode = box) => [...from.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const row = (id: number) => $(`tr[data-task="${id}"]`);
const loaded = () => until(() => $$('tr.trow').length, 'the task rows');
async function mount() {
  const store = new Store(setup().client);
  location.hash = '#/tasks';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('the row menu', () => {
  const menus = () => [...document.querySelectorAll('[role=menu]')];
  const context = (el: Element) =>
    act(async () => void el.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 50 })));
  const pressOutside = (el: Element) =>
    act(async () => {
      el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, button: 2 }));
    });

  it('shows ⋯ on row hover, opens the menu on a right-click, and one row menu is open at a time', async () => {
    await mount();
    await loaded();
    const more = $('button[aria-label="Actions"]', row(1684)!)!;
    expect(more.parentElement!.className).toContain('group-hover/trow:opacity-100'); // hidden until the row is hovered
    await context(row(1684)!);
    await until(() => menus().length === 1, 'the row menu');
    expect(text(menus()[0])).toContain('Done');
    await pressOutside(row(1688)!);
    await context(row(1688)!);
    await until(() => menus().length === 1 && menus()[0]!.getAttribute('aria-label') === '#1688', 'the second row menu alone');
  });

  it('opens the menu from the ⋯ button without opening the task', async () => {
    await mount();
    await loaded();
    await click($('button[aria-label="Actions"]', row(1684)!));
    await until(() => menus().length === 1, 'the menu');
    expect(location.hash).toBe('#/tasks');
  });
});
