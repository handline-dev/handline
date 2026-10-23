// The rail on the person's nav prefs: pins on top, the three chips, Tree or Flat, and the old local settings moving into prefs.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
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
const memory = (init: Record<string, string>): KV => {
  const data = new Map(Object.entries(init));
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

async function mount(storage: KV | null = null) {
  const s = setup();
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={storage} />));
  await until(() => box.querySelector('.rail-read'), 'the rail');
  return s;
}
const chip = (label: string) =>
  [...box.querySelectorAll<HTMLButtonElement>('.rail-read button')].find((b) => b.textContent?.startsWith(label))!;

describe('the rail on the nav prefs', () => {
  it('lists a pinned agent under Pinned and takes it off when unpinned from the row menu', async () => {
    await mount();
    await until(() => box.querySelector('[data-agent]'), 'the agent rows');
    expect(box.querySelector('.pinned-head')).toBeNull();
    await act(async () =>
      box.querySelector(`[data-agent="${ARCH}"]`)!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true })),
    );
    const item = (text: string) => [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => i.textContent === text);
    await until(() => item('Pin'), 'the Pin item');
    await act(async () => item('Pin')!.click());
    await until(() => box.querySelector('.pinned-head'), 'the Pinned section');
    expect(box.querySelector('.pinned-head + [data-agent]')?.getAttribute('data-agent')).toBe(ARCH);
  });

  it('has the Needs me chip beside All and Unread, and the View menu flips Tree to Flat', async () => {
    await mount();
    await until(() => box.querySelector('[data-agent]'), 'the agent rows');
    expect(chip('Needs me')).toBeTruthy();
    expect(chip('All').getAttribute('aria-pressed')).toBe('true');
    await act(async () => chip('Needs me').click());
    await until(() => chip('Needs me').getAttribute('aria-pressed') === 'true', 'the chip to follow the prefs');
    await act(async () => chip('All').click());
    await until(() => box.querySelector('.team-head'), 'team headings in the tree');
    await act(async () => box.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"][aria-label^="View"]')!.click());
    await until(() => document.querySelector('[role="menuitemcheckbox"]'), 'the View menu');
    const tree = [...document.querySelectorAll<HTMLElement>('[role="menuitemcheckbox"]')].find((i) => i.textContent === 'Tree')!;
    await act(async () => tree.click());
    await until(() => !box.querySelector('.team-head'), 'the flat list');
  });

  it('moves the old local settings into prefs once and clears them', async () => {
    const store = memory({ unreadOnly: '1' });
    await mount(store);
    await until(() => chip('Unread').getAttribute('aria-pressed') === 'true', 'the Unread chip from the old setting');
    expect(store.getItem('unreadOnly')).toBe('');
  });
});

describe('dragging a row', () => {
  it('drops an agent before its sibling and the order is saved, rows following it', async () => {
    await mount();
    await until(() => box.querySelectorAll('[data-agent]').length > 2, 'the agent rows');
    const names = () => [...box.querySelectorAll('[data-agent]')].map((r) => r.getAttribute('data-agent')!);
    const row = (n: string) => box.querySelector<HTMLElement>(`[data-agent="${n}"]`)!;
    const ev = (type: string, y = 0) => {
      const e = new Event(type, { bubbles: true, cancelable: true });
      Object.assign(e, { clientY: y, dataTransfer: { setData() {}, effectAllowed: '' } });
      return e;
    };
    const [a, b] = ['handline.auditor', 'handline.term-build']; // both under the architect
    expect(names().indexOf(a!)).toBeLessThan(names().indexOf(b!));
    await act(async () => {
      row(b!).dispatchEvent(ev('dragstart'));
      row(a!).dispatchEvent(ev('dragover', -1));
      row(a!).dispatchEvent(ev('drop', -1));
    });
    await until(() => names().indexOf(b!) < names().indexOf(a!), 'the new order');
  });
});
