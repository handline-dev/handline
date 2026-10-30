// The sidebar's search box opens the palette, and Tasks carries a count when tasks wait on the person; the agent menu marks what is unseen as read.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
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

let root: Root | null = null;
let box: HTMLElement;
let keep = () => {};
async function mount(s: ReturnType<typeof setup>) {
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
}
afterEach(async () => {
  keep();
  keep = () => {};
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('the sidebar search and the agent menu', () => {
  it('shows a search box with its shortcut that opens the palette', async () => {
    const s = setup();
    await mount(s);
    await until(() => box.querySelector('.rail-search'), 'the search box');
    expect(box.querySelector('.rail-search')!.textContent).toContain('⌘K');
    expect(document.querySelector('.palette')).toBeNull();
    await act(async () => box.querySelector<HTMLElement>('.rail-search')!.click());
    await until(() => document.querySelector('.palette'), 'the palette');
  });

  it('offers Mark as read in an agent menu only while something from it is unseen', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = { founder_unread: 2 } as never;
    await mount(s);
    const menu = async () => {
      await act(
        async () =>
          void box.querySelector(`[data-agent="${ARCH}"] .row-main`)!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true })),
      );
      await until(() => document.querySelector('[role=menu]'), 'the menu');
      return [...document.querySelectorAll('[role=menuitem]')].map((i) => i.textContent);
    };
    await until(() => box.querySelector(`[data-agent="${ARCH}"] .unread`), 'the unseen count');
    expect(await menu()).toContain('Mark as read');
  });
});
