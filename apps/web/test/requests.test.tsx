// The Requests page (#/requests): the sidebar entry and the New request dialog, on the fake daemon. What waits on the founder is in my-turn.test.tsx.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 300) {
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
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const button = (label: string) => $$('button').find((b) => text(b).trim() === label || b.title === label);

async function mount(hash: string, s = setup()) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('the Requests page', () => {
  it('has a sidebar entry that opens the page, and a New request dialog', async () => {
    await mount('#/tasks');
    await until(() => button('Requests') || $$('.rail-nav button').find((b) => text(b).includes('Requests')), 'the sidebar entry');
    await click($$('.rail-nav button').find((b) => text(b).includes('Requests')));
    expect(location.hash).toBe('#/requests');
    await until(() => button('New request'), 'the page');
    await click(button('New request'));
    await until(() => document.querySelector('.ui-dialog'), 'the dialog');
    expect(text(document.querySelector('.ui-dialog'))).toContain('New request');
    expect(text(document.querySelector('.ui-dialog'))).not.toContain('Priority');
  });
});
