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

  it('Later puts a task item off until a time: it leaves the list and the count, waits in the Later fold with when it returns, and comes back with the x', async () => {
    const s = setup();
    s.daemon.tasks.seed({ id: 9201, from: 'handline.architect', to: 'human', title: 'Decide the thing', state: 'queued' });
    await mount('#/requests', s);
    await until(() => $$('[data-card-row]').some((r) => text(r).includes('Decide the thing')), 'the rows');
    const row = $$('[data-card-row]').find((r) => /^Open T\d+/.test(r.querySelector('button')?.getAttribute('aria-label') ?? ''))!;
    const key = row.dataset.cardRow!;
    const before = $$('[data-card-row]').length;
    await click(row.querySelector('button'));
    await until(() => button('Later'), 'the Later button');
    await click(button('Later'));
    await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => text(i).startsWith('Tomorrow')));
    await until(() => $$('[data-card-row]').length === before - 1, 'the item leaving the list');
    expect($$('[data-card-row]').some((r) => r.dataset.cardRow === key)).toBe(false);
    await click($$('section[aria-label="Later"] > button')[0]);
    await until(() => $$('[data-later]').length === 1, 'the fold');
    expect(text($$('[data-later]')[0]!.querySelector('button'))).toMatch(/\d/); // when it returns
    await click($$('[data-later] button')[0]);
    await until(() => button('Back to Requests now'), 'the x');
    await click(button('Back to Requests now'));
    await until(() => text(document.body).includes('Back in Requests'), 'the toast');
    await click(button('Back to Requests'));
    await until(() => $$('[data-card-row]').length === before, 'the item back');
  });
});
