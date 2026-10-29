// The My turn page: cards from the inbox, agents that need the person, most urgent first.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

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
/** The list's rows: a row only opens its item, which is answered in the detail. */
const listRows = () => [...box.querySelectorAll<HTMLElement>('[data-card-row] button')];
const pick = async (match: (r: HTMLElement) => boolean, what: string) => {
  await until(() => listRows().find(match), what);
  await act(async () => void listRows().find(match)!.click());
};
const ofTask = (id: number) => (r: HTMLElement) => r.getAttribute('aria-label') === `Open T${id}`;
const ofKey = (key: string) => (r: HTMLElement) => r.closest('[data-card-row]')!.getAttribute('data-card-row') === key;

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
    await pick(ofKey(`agent:${ARCH}`), 'the dialog row');
    await until(() => cards().some((c) => c.dataset.kind === 'dialog'), 'the dialog card');
    const dialog = cards().find((c) => c.dataset.kind === 'dialog')!;
    expect(dialog.textContent).toContain('Trust this folder?');
    const names = [...dialog.querySelectorAll('button')].map((b) => b.getAttribute('aria-label') ?? b.textContent);
    expect(names).toContain('Open terminal');
    expect(names).not.toContain('Allow'); // the answer is the terminal; the rest only name or open the agent
    expect(box.querySelector('.rail-nav')!.textContent).toMatch(/My turn\d+/);
  });
  it('lists an agent with unread messages as a row under Messages, and a click opens its chat at the first unread', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = { founder_unread: 3, founder_seen: 4, last_to_founder: { text: 'The build is green', at: new Date().toISOString() } };
    location.hash = '#/my-turn';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
    });
    await until(() => box.querySelector(`[data-row="${ARCH}"]`), 'the message row');
    const row = box.querySelector<HTMLElement>(`[data-row="${ARCH}"]`)!;
    expect(row.textContent).toContain('The build is green');
    expect(row.textContent).toContain('3');
    expect(box.querySelector('[aria-label="Messages"]')!.contains(row)).toBe(true);
    // The Chat tab drops `?m=` once it has scrolled to the message, so the link is read as the click sets it, not after.
    const seen: string[] = [];
    const note = () => void seen.push(location.hash);
    addEventListener('hashchange', note);
    await act(async () => void row.querySelector('button')!.click());
    removeEventListener('hashchange', note);
    expect(seen).toContain(`#/agents/${encodeURIComponent(ARCH)}?m=5`);
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
    s.daemon.inbox.add({
      kind: 'decision',
      agent: ARCH,
      title: `Decide ${id}`,
      body: 'Pick A or B.',
      task: id,
      created: new Date().toISOString(),
      read: false,
    });
  };

  /** A decision about a task tagged review, so its card offers Approve and Ask for change. */
  const review = (s: ReturnType<typeof setup>, id: number) => {
    s.daemon.tasks.seed({ id, from: ME, to: ARCH, title: `Review ${id}`, state: 'doing', tags: ['review'] });
    s.daemon.inbox.add({
      kind: 'decision',
      agent: ARCH,
      title: `Look ${id}`,
      body: 'Is this right?',
      task: id,
      created: new Date().toISOString(),
      read: false,
    });
  };
  const reviewCard = (id: number) => cards().find((c) => c.textContent?.includes(`Review ${id}`));

  it('a review card has Approve and Ask for change in place of Done; Approve closes the task', async () => {
    const s = setup();
    review(s, 9101);
    await open(s);
    await pick(ofTask(9101), 'the review row');
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
    await pick(ofTask(9102), 'the review row');
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
    await pick(ofTask(9002), 'the decision row');
    await until(() => cards()[0], 'the item');
    await click(button(cards()[0]!, 'Open T9002'));
    await until(() => box.querySelector('.tdetail'), 'the task detail');
    expect(box.querySelector('.my-turn-split .my-turn')).not.toBeNull(); // beside the list, not instead of it
    expect(listRows().find(ofTask(9002))!.getAttribute('aria-current')).toBe('true');
    await until(() => box.querySelector('.tdetail .tbody'), 'the task body');
    // jsdom does not match :focus-visible, so the cascade is checked in the rules: a later, more specific one than the base ring.
    const css = readFileSync(join(process.cwd(), 'src/styles/base.css'), 'utf8');
    expect(css.indexOf('.tbody:focus-visible')).toBeGreaterThan(css.indexOf('\n:focus-visible'));
    expect(/\.tbody:focus-visible\s*\{\s*outline:\s*none;/.test(css)).toBe(true);
    expect(box.querySelector('.tdetail .tbody')).not.toBeNull();
    await click(box.querySelector('.tdetail .back') ?? undefined);
    expect(box.querySelector('.tdetail')).toBeNull();
  });

  it('on a narrow window the task takes the page: one Back, to My turn, in the head', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    keep = () => void vi.unstubAllGlobals();
    const s = setup();
    decision(s, 9002);
    await open(s);
    await pick(ofTask(9002), 'the decision row');
    await until(() => cards()[0], 'the item');
    await click(button(cards()[0]!, 'Open T9002'));
    await until(() => box.querySelector('.tdetail .tbody'), 'the task body');
    expect(box.querySelector('.my-turn-split')).toBeNull(); // instead of the list, not beside it
    const backs = [...box.querySelectorAll('.back')];
    expect(backs.map((b) => b.getAttribute('title'))).toEqual(['Back to My turn']); // one Back, not a second one in the article
    expect(box.querySelector('.tdetail .back')).toBeNull();
    await click(backs[0]);
    expect(box.querySelector('.tdetail')).toBeNull();
    expect(cards().length).toBeGreaterThan(0);
  });

  it('Reply sends the written answer of a decision', async () => {
    const s = await open();
    await pick(ofKey('item:3'), 'the decision row');
    await until(() => box.querySelector('[data-card="item:3"]'), 'the decision card');
    const c = box.querySelector('[data-card="item:3"]')!;
    expect(c.querySelector('[role=button]')).toBeNull(); // no task to open, and no clamped text to unfold
    expect(c.textContent).not.toContain('Show more');
    await click(button(c, 'Reply'));
    await type(c.querySelector('input')!, 'Yes, relay it.');
    await click(button(c, 'Send reply'));
    await until(() => s.daemon.inbox.get(3).read, 'the daemon');
  });

  it('a permission prompt without yes/no options is a card with only Open terminal', async () => {
    await open();
    await pick(ofKey('item:4'), 'the approval row');
    await until(() => box.querySelector('[data-card="item:4"]'), 'the approval card');
    const c = box.querySelector('[data-card="item:4"]')!;
    expect(c.getAttribute('data-kind')).toBe('approval');
    expect(button(c, 'Allow')).toBeUndefined();
  });
});
