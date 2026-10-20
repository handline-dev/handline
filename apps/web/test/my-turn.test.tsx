// The My turn page: cards from the inbox, agents that need the person, most urgent first.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const ME = 'web-test'; // the name the fake setup gives its client
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
let keep = () => {};
afterEach(async () => {
  keep();
  keep = () => {};
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});
const cards = () => [...box.querySelectorAll<HTMLElement>('.my-turn-card')];

describe('my turn', () => {
  it('shows a dialog agent as one card with only Open terminal, and counts it in the rail', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = {
      needs_you: { kind: 'dialog', since: new Date(Date.now() - 5 * 60_000).toISOString(), text: 'Trust this folder?', source: 'hook' },
    };
    location.hash = '#/my-turn';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
    });
    await until(() => cards().some((c) => c.dataset.kind === 'dialog'), 'the dialog card');
    const dialog = cards().find((c) => c.dataset.kind === 'dialog')!;
    expect(dialog.textContent).toContain('Trust this folder?');
    expect([...dialog.querySelectorAll('button')].map((b) => b.getAttribute('aria-label') ?? b.textContent)).toEqual(['Open terminal']);
    expect(box.querySelector('.rail-nav')!.textContent).toMatch(/Inbox \d+/);
  });
  async function open(s = setup()) {
    s.daemon.tasks.verbs = true;
    location.hash = '#/my-turn';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
    });
    return s;
  }
  const label = (b: Element) => b.getAttribute('title') ?? b.getAttribute('aria-label') ?? b.textContent ?? '';
  const button = (c: Element, name: string) => [...c.querySelectorAll('button')].find((b) => label(b) === name);
  const click = (el: Element | undefined) => act(async () => void (el as HTMLElement).click());
  const type = (el: Element, v: string) =>
    act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, v);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  /** A decision about a task, so its card has a task to open. */
  const decision = (s: ReturnType<typeof setup>, id: number) => {
    s.daemon.tasks.seed({ id, from: ME, to: ARCH, title: `Task ${id}`, state: 'doing' });
    s.daemon.inbox.add({ kind: 'decision', agent: ARCH, title: `Decide ${id}`, body: 'Pick A or B.', task: id, created: new Date().toISOString(), read: false });
  };

  /** A decision about a task tagged review, so its card offers Approve and Ask for change. */
  const review = (s: ReturnType<typeof setup>, id: number) => {
    s.daemon.tasks.seed({ id, from: ME, to: ARCH, title: `Review ${id}`, state: 'doing', tags: ['review'] });
    s.daemon.inbox.add({ kind: 'decision', agent: ARCH, title: `Look ${id}`, body: 'Is this right?', task: id, created: new Date().toISOString(), read: false });
  };
  const reviewCard = (id: number) => cards().find((c) => c.textContent?.includes(`Review ${id}`));

  it('a review card has Approve and Ask for change in place of Done; Approve closes the task', async () => {
    const s = setup();
    review(s, 9101);
    await open(s);
    await until(() => reviewCard(9101), 'the review card');
    const c = reviewCard(9101)!;
    expect(button(c, 'Done')).toBeUndefined();
    await click(button(c, 'Approve'));
    await until(() => s.daemon.tasks.find(9101).state === 'done', 'the task done');
    expect(s.daemon.tasks.find(9101).evidence).toBe('Approved');
  });

  it('Ask for change comments the words and closes the task with them', async () => {
    const s = setup();
    review(s, 9102);
    await open(s);
    await until(() => reviewCard(9102), 'the review card');
    await click(button(reviewCard(9102)!, 'Ask for change'));
    await until(() => document.querySelector('textarea[aria-label="What should change"]'), 'the box');
    await act(async () => {
      const t = document.querySelector('textarea[aria-label="What should change"]') as HTMLTextAreaElement;
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(t, 'Wider margin');
      t.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click([...document.querySelectorAll('button')].find((b) => label(b).startsWith('Send')));
    await until(() => s.daemon.tasks.find(9102).state === 'done', 'the task done');
    expect(s.daemon.tasks.find(9102).evidence).toBe('Change asked: Wider margin');
    expect(s.daemon.tasks.find(9102).memos?.map((m) => m.text)).toEqual(['Wider margin']);
  });

  it('a row opens its task in a detail beside the list on a wide window; Back closes it', async () => {
    const media = (matches: boolean) => vi.stubGlobal('matchMedia', () => ({ matches, addEventListener() {}, removeEventListener() {} }));
    media(true);
    keep = () => void vi.unstubAllGlobals();
    const s = setup();
    decision(s, 9002);
    await open(s);
    const card = () => cards().find((c) => c.textContent?.includes('Task 9002'));
    await until(card, 'the decision card');
    const c = card()!;
    await click(c.querySelector('[role=button]') ?? undefined);
    await until(() => box.querySelector('.tdetail'), 'the task detail');
    expect(box.querySelector('.my-turn-split .my-turn')).not.toBeNull(); // beside the list, not instead of it
    expect(card()!.getAttribute('aria-current')).toBe('true');
    await until(() => box.querySelector('.tdetail .tbody'), 'the task body');
    // jsdom does not match :focus-visible, so the cascade is checked in the rules: a later, more specific one than the base ring.
    const css = readFileSync(join(process.cwd(), 'src/styles/base.css'), 'utf8');
    expect(css.indexOf('.tbody:focus-visible')).toBeGreaterThan(css.indexOf('\n:focus-visible'));
    expect(/\.tbody:focus-visible\s*\{\s*outline:\s*none;/.test(css)).toBe(true);
    expect(box.querySelector('.tdetail .tbody')).not.toBeNull();
    await click(box.querySelector('.tdetail .back') ?? undefined);
    expect(box.querySelector('.tdetail')).toBeNull();
  });

  it('Reply sends the written answer of a decision', async () => {
    const s = await open();
    await until(() => box.querySelector('[data-card="item:3"]'), 'the decision card');
    const c = box.querySelector('[data-card="item:3"]')!;
    expect(c.querySelector('[role=button]')).toBeNull(); // no task to open, and no clamped text to unfold
    expect(c.querySelector('.line-clamp-3')).toBeNull();
    await click(button(c, 'Reply'));
    await type(c.querySelector('input')!, 'Yes, relay it.');
    await click(button(c, 'Send reply'));
    await until(() => s.daemon.inbox.get(3).read, 'the daemon');
  });

  it('a permission prompt without yes/no options is a card with only Open terminal', async () => {
    await open();
    await until(() => box.querySelector('[data-card="item:4"]'), 'the approval card');
    const c = box.querySelector('[data-card="item:4"]')!;
    expect(c.getAttribute('data-kind')).toBe('approval');
    expect(button(c, 'Allow')).toBeUndefined();
  });
});
