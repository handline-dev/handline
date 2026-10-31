// Requests: "From you" lists the founder's own requests by what is happening to them, with how far each is and who holds it; and the weekly-loss
// offer is an item of For you, answered with Start or Dismiss, no longer a card over the bell.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { applySeeds } from '@handline/fake-daemon/seeds';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const button = (word: string) => [...box.querySelectorAll('button')].find((b) => text(b).startsWith(word));

async function mount(s: ReturnType<typeof setup>) {
  location.hash = '#/requests';
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

describe('Requests: From you', () => {
  it('lists the requests in their groups with done of total, the task number and the time', async () => {
    const s = setup();
    applySeeds(s.daemon, 'requests');
    await mount(s);
    await until(() => button('From you'), 'the switch');
    await click(button('From you'));
    await until(() => $('[data-request-row]'), 'the rows');
    const group = (name: string) => $(`[role="group"][aria-label="${name}"]`);
    expect(text(group('Waiting on you'))).toContain('Which build is live?');
    expect(text(group('Being worked'))).toContain('Show the usage graph first');
    expect(text(group('Being worked'))).toContain('1 of 3'); // one done, two open
    expect(text(group('Being worked'))).toContain('T6071'); // local-ref-ok
    expect(text(group('Not taken yet'))).toContain('Why is the build so slow today?');
    expect(group('Done')).toBeNull(); // closed ones are behind Closed
    await click(button('Closed'));
    expect(text(group('Done'))).toContain('Settings in one place');
  });

  it('opens a request with the line that says how far it is and who holds it now', async () => {
    const s = setup();
    applySeeds(s.daemon, 'requests');
    await mount(s);
    await until(() => button('From you'), 'the switch');
    await click(button('From you'));
    await until(() => $('[data-request-row]'), 'the rows');
    await click($('[data-request-row="6068"] button'));
    await until(() => $('.request-line'), 'the line');
    expect(text($('.request-line'))).toBe('2 of 3 done · now pc · in review · #630');
  });
});

describe('Requests: the weekly-loss offer', () => {
  it('is an item of For you with its spare tasks, and Start answers the task', async () => {
    const s = setup({ client: 'founder' });
    s.daemon.tasks.verbs = true;
    applySeeds(s.daemon, 'spare-notice');
    await mount(s);
    await until(() => $('[data-card-row="task:7100"] button'), 'the offer row');
    await click($('[data-card-row="task:7100"] button'));
    await until(() => $('.notice-spare'), 'the offer in Requests');
    expect(document.querySelector('.toasts .notice-spare')).toBeNull(); // not over the bell
    expect(text($('.notice-spare'))).toContain('Tidy the gap list');
    expect(text($('.notice-spare'))).toContain('and 1 more');
    expect(button('Later')).toBeUndefined(); // an agent is not stopped on it, but it is answered, not put off
    await click(button('Start 4 tasks'));
    await act(async () => void (await new Promise((r) => setTimeout(r, 5200)))); // the answer is held five seconds, for Undo
    await until(() => s.daemon.tasks.get({ task: 7100 }).state === 'done', 'the task answered');
    expect(s.daemon.tasks.get({ task: 7100 }).evidence).toBe('start');
  }, 15000);
});
