// The File tab: the + menu opens it, a path shows the file, Edit then Save writes it back; against the fake daemon.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
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
const click = (el: Element) => act(async () => void (el as HTMLElement).click());

async function mount(s = setup(), agent = ARCH) {
  const store = new Store(s.client);
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
  await until(() => box.querySelector('[aria-label="Add a tab"]'), 'the tab strip');
  return s;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

const item = (name: string) => [...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent === name);
const btn = (name: string) => [...box.querySelectorAll<HTMLElement>('.file-tab button')].find((b) => b.textContent === name)!;

function typeInto(el: HTMLInputElement | HTMLTextAreaElement, v: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, v);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('File tab', () => {
  it('opens a path, edits it and saves it back', async () => {
    const s = setup();
    await mount(s);
    await click(box.querySelector('[aria-label="Add a tab"]')!);
    await click(item('File')!);
    await until(() => box.querySelector('.file-tab'), 'the tab');
    await act(async () => typeInto(box.querySelector('.file-tab input')!, 'docs/pc-app-gaps.md'));
    await click(btn('Open'));
    await until(() => btn('Edit'), 'the Edit button');
    await click(btn('Edit'));
    const area = box.querySelector<HTMLTextAreaElement>('.file-tab textarea')!;
    await act(async () => typeInto(area, `${area.value}\nnew line`));
    await click(btn('Save'));
    await until(() => btn('Edit') && !box.querySelector('.file-tab textarea'), 'saved');
    await until(() => box.querySelector('.file-tab')?.textContent?.includes('new line'), 'the new text shown');
  });
});
