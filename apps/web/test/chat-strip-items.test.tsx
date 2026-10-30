// The strip's branch (a click copies its name) and the context gauge's maximum (a pick is applied at once).
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const text = (el: Element | null) => el?.textContent ?? '';

async function mount() {
  const s = setup();
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  await until(() => $('.chat-strip'), 'the strip');
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
  vi.unstubAllGlobals();
});

describe('the strip branch and context maximum', () => {
  it('a click on the branch copies its name and says Copied', async () => {
    const write = vi.fn(() => Promise.resolve());
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: write } });
    const s = await mount();
    const branch = s.daemon.sessions.get(ARCH)!.fx.branch;
    await until(() => $('.chat-strip .branch button'), 'the branch');
    await act(async () => $('.chat-strip .branch button')!.click());
    await until(() => document.querySelector('[role="menuitem"]'), 'the branch menu');
    await act(async () => document.querySelector<HTMLElement>('[role="menuitem"]')!.click());
    expect(write).toHaveBeenCalledWith(branch);
    await until(() => text(document.body).includes('Copied'), 'the Copied state');
  });

  it('the context menu offers fresh-start points and a pick is applied at once', async () => {
    const s = await mount();
    await until(() => $('.chat-strip [aria-label="Context window details"]'), 'the context gauge');
    await act(async () => $('.chat-strip [aria-label="Context window details"]')!.click());
    const items = () => [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')];
    await until(() => items().length, 'the maximums');
    await act(async () =>
      items()
        .find((i) => text(i).includes('150k'))!
        .click(),
    );
    await until(() => s.daemon.sessions.get(ARCH)!.settings.context_k === '150', 'the maximum applied');
    // The daemon sends no event for it: the header reads the new maximum because the strip is read again.
    await act(async () => $('.chat-strip [aria-label="Context window details"]')!.click());
    await until(() => document.body.textContent?.includes('of 150k'), 'the header with the new maximum');
  });
});
