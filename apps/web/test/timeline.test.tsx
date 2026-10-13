// The Timeline tab: newest first, Older until more is false, opened by the stalled badge; against the fake daemon.
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
const entries = () => [...box.querySelectorAll<HTMLElement>('[data-entry]')].map((e) => Number(e.dataset.entry));
const older = () => [...box.querySelectorAll<HTMLButtonElement>('.timeline-tab button')].find((b) => b.textContent === 'Older');
const click = (el: Element) => act(async () => void (el as HTMLElement).click());

async function mount(s = setup()) {
  const store = new Store(s.client);
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
  await until(() => box.querySelector('[data-tab^="terminal"]'), 'the terminal tab');
  return s;
}
/** Each take or release of the input is one recorded event of the agent. */
const record = async (s: ReturnType<typeof setup>, n: number) => {
  await s.client.start();
  for (let i = 1; i <= n; i++) await s.client.call(i % 2 ? 'input.take' : 'input.release', { agent: ARCH, request_id: `r${i}` });
};

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('Timeline tab', () => {
  it('lists the newest first, and Older pages until there is no more', async () => {
    const s = setup();
    await record(s, 60);
    await mount(s);
    await click(box.querySelector('[aria-label="Add a tab"]')!);
    await click([...box.querySelectorAll('[role=menuitem], button')].find((b) => b.textContent === 'Timeline')!);
    await until(() => entries().length === 50, 'the first page');
    expect(entries()[0]).toBe(60);
    expect(entries().at(-1)).toBe(11);
    await click(older()!);
    await until(() => entries().length === 60, 'the older page');
    expect(entries()).toEqual(Array.from({ length: 60 }, (_, i) => 60 - i));
    expect(older()).toBeUndefined();
  });

  it('a stalled agent’s badge opens it', async () => {
    const s = setup();
    await record(s, 3);
    s.daemon.sessions.get(ARCH)!.state = 'stalled';
    await mount(s);
    await click(box.querySelector('[data-tab^="terminal"] .badge.stalled')!);
    await until(() => box.querySelector('.timeline-tab'), 'the timeline tab');
    await until(() => entries().length === 3, 'the entries');
    expect(entries()).toEqual([3, 2, 1]);
  });
});
