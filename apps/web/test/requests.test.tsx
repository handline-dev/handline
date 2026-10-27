// The Requests page (#/requests): the founder's requests with their status, a message that is not a task yet made a request or marked no work, and the
// sidebar entry, on the fake daemon.
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
  it('lists the requests with their status, holders and counts, and filters by status', async () => {
    const s = setup();
    s.daemon.requests.add({
      title: 'Show the usage first',
      task: 6071,
      status: 'doing',
      holders: ['handline.pc'],
      open: 2,
      done: 1,
      prs: [628],
    });
    s.daemon.requests.add({ title: 'Which build is live?', task: 6020, status: 'waiting_on_you', agent: 'handline', unread_comments: 2 });
    await mount('#/requests', s);
    await until(() => $$('.request-row').length === 2, 'two requests');
    const first = $('[data-request="6020"]')!;
    expect(first.dataset.status).toBe('waiting_on_you');
    expect(text(first)).toContain('2 new comments');
    expect(text($('[data-request="6071"]'))).toContain('2 open · 1 done');
    expect(text($('[data-request="6071"]'))).toContain('#628');
    await click(button('Doing'));
    expect($$('.request-row').map((r) => r.dataset.request)).toEqual(['6071']);
  });

  it('opens the task of a request', async () => {
    const s = setup();
    s.daemon.requests.add({ title: 'Show the usage first', task: 1684, status: 'doing' });
    await mount('#/requests', s);
    await until(() => $('.request-row'), 'the request');
    await click($('[aria-label="Open T1684"]'));
    expect(location.hash).toBe('#/tasks/1684');
  });

  it("lists a message that is not a task yet without buttons: turning it into a request is the lead's job", async () => {
    const s = setup();
    s.daemon.requests.add({ title: 'Why is the build slow?', kind: 'message' });
    s.daemon.requests.add({ title: 'thanks', kind: 'message', status: 'no_work', reason: 'a thank-you' });
    await mount('#/requests', s);
    await until(() => $$('.request-row').length === 2, 'two messages');
    expect($$('.request-row button')).toHaveLength(0);
    expect(text($('[data-status="no_work"]'))).toContain('Nothing to do');
    expect(text($('[data-status="no_work"]'))).toContain('a thank-you');
  });

  it('has a sidebar entry that opens the page, and a New request dialog', async () => {
    await mount('#/my-turn');
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
