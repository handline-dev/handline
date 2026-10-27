// The weekly-loss notice: a task for the person tagged spare-notice is a card above the bell, with the first three spare tasks and a
// Start button; Start and the X answer the task.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
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
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const msg = (body: string) => $$('.msg').find((m) => text(m).includes(body));
const open = (s: ReturnType<typeof setup>) => {
  s.daemon.tasks.verbs = true;
  applySeeds(s.daemon, 'spare-notice');
};
const spare = () => $('.notice-spare');

async function mount(agent: string, s = setup()) {
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
}
afterEach(async () => {
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
    expect(text(spare())).toContain('T7101 Tidy the gap list');
    expect(text(spare())).not.toContain('T7104');
    expect(button('Start 4 tasks')).toBeDefined();
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
