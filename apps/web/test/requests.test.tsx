// The Requests page (#/requests): the sidebar entry and the New request dialog, on the fake daemon. What waits on the founder is in my-turn.test.tsx.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
  vi.unstubAllGlobals();
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

  /** The page with one task of the founder's to put off (and a second open one to wait for); opens it. */
  async function withTask() {
    const s = setup();
    s.daemon.tasks.seed({ id: 9201, from: 'handline.architect', to: 'human', title: 'Decide the thing', state: 'queued' });
    s.daemon.tasks.seed({ id: 9202, from: 'handline.architect', to: 'handline.auditor', title: 'The other task', state: 'doing' });
    await mount('#/requests', s);
    await until(() => $$('[data-card-row]').some((r) => text(r).includes('Decide the thing')), 'the rows');
    await click(
      $$('[data-card-row]')
        .find((r) => text(r).includes('Decide the thing'))!
        .querySelector('button'),
    );
    await until(() => button('Later'), 'the Later button');
    return s;
  }
  const menu = (name: string) =>
    click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => text(i).startsWith(name)));
  const stored = (s: ReturnType<typeof setup>) =>
    (s.daemon as unknown as { admin: { prefs: { nav: { later: Record<string, unknown> } } } }).admin.prefs.nav.later;
  const setField = (el: HTMLInputElement | HTMLSelectElement, value: string) =>
    act(async () => {
      const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
      el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
    });
  const dialogGo = (form: string) => document.querySelector<HTMLButtonElement>(`button[form="${form}"]`)!;

  it('Pick a time sends until, and refuses a time that has passed', async () => {
    const s = await withTask();
    await click(button('Later'));
    await menu('Pick a time');
    await until(() => document.querySelector('input[aria-label="Date and time"]'), 'the form');
    const field = document.querySelector<HTMLInputElement>('input[aria-label="Date and time"]')!;
    await setField(field, '2020-01-01T10:00');
    expect(dialogGo('later-time').disabled).toBe(true);
    await setField(field, '2099-01-01T10:00');
    expect(dialogGo('later-time').disabled).toBe(false);
    await click(dialogGo('later-time'));
    await until(() => Object.keys(stored(s)).length === 1, 'the deferral');
    expect(Object.values(stored(s))[0]).toEqual({ until: new Date('2099-01-01T10:00').toISOString() });
  });

  it('When a task is done sends after_task and offers every open task but its own', async () => {
    const s = await withTask();
    await click(button('Later'));
    await menu('When a task is done');
    await until(() => document.querySelector('select[aria-label="Task to wait for"]'), 'the form');
    const pick = document.querySelector<HTMLSelectElement>('select[aria-label="Task to wait for"]')!;
    const values = [...pick.options].map((o) => o.value);
    expect(values).toContain('9202');
    expect(values).not.toContain('9201');
    await setField(pick, '9202');
    await click(dialogGo('later-task'));
    await until(() => Object.keys(stored(s)).length === 1, 'the deferral');
    expect(Object.values(stored(s))[0]).toEqual({ after_task: 9202 });
  });

  it('Undo on the toast puts the previous deferral back, and a put-off item drops the count in the sidebar and the page', async () => {
    const s = await withTask();
    const badge = () => Number(text($$('.rail-nav button').find((b) => text(b).includes('Requests'))).replace(/\D/g, ''));
    const before = badge();
    await click(button('Later'));
    await menu('Tomorrow');
    await until(() => Object.keys(stored(s)).length === 1, 'tomorrow');
    const tomorrow = Object.values(stored(s))[0];
    await until(() => badge() === before - 1, 'the sidebar count dropping');
    await until(() => $$('[data-card-row]').length > 0, 'the list');
    expect($$('[data-card-row]').some((r) => text(r).includes('Decide the thing'))).toBe(false); // out of the page's groups
    await click($$('section[aria-label="Later"] > button')[0]);
    await click($$('[data-later] button')[0]);
    await until(() => $$('button').some((b) => text(b).startsWith('Later:')), 'the opened put-off item');
    await click($$('button').find((b) => text(b).startsWith('Later:')));
    await menu('Next Mon');
    await until(() => JSON.stringify(Object.values(stored(s))[0]) !== JSON.stringify(tomorrow), 'next Monday');
    await click($$('button').find((b) => text(b) === 'Undo'));
    await until(() => JSON.stringify(Object.values(stored(s))[0]) === JSON.stringify(tomorrow), 'tomorrow again');
  });

  it('on a wide window a put-off item opens from the Later fold and stays open', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: true, media: q, addEventListener() {}, removeEventListener() {} }));
    const s = setup();
    s.daemon.tasks.seed({ id: 9201, from: 'handline.architect', to: 'human', title: 'Decide the thing', state: 'queued' });
    await mount('#/requests', s);
    await until(() => $$('[data-card-row]').some((r) => text(r).includes('Decide the thing')), 'the rows');
    await click(
      $$('[data-card-row]')
        .find((r) => text(r).includes('Decide the thing'))!
        .querySelector('button'),
    );
    await until(() => button('Later'), 'the Later button');
    await click(button('Later'));
    await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => text(i).startsWith('Tomorrow')));
    await until(() => $$('section[aria-label="Later"] > button')[0], 'the fold');
    await click($$('section[aria-label="Later"] > button')[0]);
    await click($$('[data-later] button')[0]);
    await wait(100);
    expect(text(document.querySelector('article'))).toContain('Decide the thing'); // not the first request
    expect($$('button').some((b) => text(b).startsWith('Later:'))).toBe(true);
  });
});
