// The Tasks page's My requests chip: the founder's own open tasks in their groups, each with one line (how far, who works now, or what it waits on).
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
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
const chip = () => [...box.querySelectorAll('button')].find((b) => text(b).startsWith('My requests'));

async function mount(s: ReturnType<typeof setup>) {
  location.hash = '#/tasks';
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

const seed = () => {
  const s = setup();
  s.daemon.tasks.seed({ id: 7031, from: 'human', to: 'handline.pc-chat', title: 'Decide the status line', state: 'doing' });
  s.daemon.tasks.seed({
    id: 7121,
    from: 'human',
    to: 'handline.pc-chat',
    title: 'Status line shape',
    state: 'doing',
    blockers: [{ task: 7031, reason: '' }],
  });
  s.daemon.requests.add({
    title: 'Keyboard on the request screen',
    task: 7110,
    open: 2,
    done: 3,
    holders: ['handline.daemon-core'],
    now: 'handline.daemon-core',
  });
  s.daemon.requests.add({
    title: 'Status line shape',
    task: 7121,
    open: 1,
    done: 0,
    holders: ['handline.pc-chat'],
    now: 'handline.pc-chat',
    blocked: true,
  });
  s.daemon.requests.add({ title: 'Closed tasks by day', task: 7133, open: 0, done: 0 });
  s.daemon.requests.add({ title: 'Settings left bar', task: 7102, status: 'waiting_on_you', open: 1, done: 4 });
  s.daemon.requests.add({ title: 'Already done', task: 7001, status: 'done', done: 2 });
  return s;
};

describe('the My requests chip', () => {
  it('counts the open requests and lists them in their groups with their line', async () => {
    await mount(seed());
    await until(() => text(chip()).includes('4'), 'the count');
    await click(chip());
    await until(() => $('[data-request]'), 'the rows');
    const group = (name: string) => $(`section[aria-label="${name}"]`);
    expect(text(group('Waiting on you'))).toContain('Settings left bar');
    expect(text(group('Waiting on you'))).toContain('waiting on you'); // not 'not taken yet'
    expect(text($('[data-request="7110"]'))).toContain('3 of 5 done · now daemon-core');
    expect(text($('[data-request="7133"]'))).toContain('not taken yet');
    expect(text(group('Blocked'))).toContain('Status line shape');
    expect(chip()!.getAttribute('aria-pressed')).toBe('true');
    expect(chip()!.querySelector('svg')).not.toBeNull(); // the ×, so it can be taken off
    expect($('[data-request="7001"]')).toBeNull(); // a done one is not open
  });

  it('says what a blocked request waits on, and the id opens it', async () => {
    await mount(seed());
    await until(() => chip(), 'the chip');
    await click(chip());
    await until(() => $('[data-line="blocked"]'), 'the blocked line');
    expect(text($('[data-line="blocked"]'))).toBe('Blocked on T7031'); // local-ref-ok
    await click($('[data-line="blocked"] button'));
    expect(location.hash).toBe('#/tasks/7031');
  });

  it('opens a request as a row of Tasks does, and Closed turns the chip off', async () => {
    await mount(seed());
    await until(() => chip(), 'the chip');
    await click(chip());
    await until(() => $('[data-request="7110"]'), 'the rows');
    await click($('[data-request="7110"]'));
    expect(location.hash).toBe('#/tasks/7110');
    location.hash = '#/tasks';
    await until(() => [...box.querySelectorAll('button')].find((b) => text(b) === 'Closed'), 'Closed');
    await click(chip());
    await until(() => $('[data-request]'), 'the list on');
    await click([...box.querySelectorAll('[role="radio"]')].find((b) => text(b) === 'Closed'));
    await until(() => !$('[data-request]'), 'the list off');
    expect(chip()!.getAttribute('aria-pressed')).toBe('false');
  });
});
