// The Requests page: what waits on the person, grouped Allow, Decide, Review and Do by hand, as a list beside the open item.
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
    location.hash = '#/requests';
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
    expect(box.querySelector('.rail-nav')!.textContent).toMatch(/Requests\d+/);
  });
  async function open(s = setup()) {
    s.daemon.tasks.verbs = true;
    location.hash = '#/requests';
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

  it("a worktree-removal card has Keep it, Save a copy and Remove anyway; each closes the task with the daemon's words", async () => {
    const s = setup();
    s.daemon.tasks.seed({ id: 9103, from: ME, to: ARCH, title: 'Remove bob worktree', state: 'doing', tags: ['worktree-removal'] });
    s.daemon.inbox.add({
      kind: 'decision',
      agent: ARCH,
      title: 'Remove',
      body: 'Path: /x',
      task: 9103,
      created: new Date().toISOString(),
      read: false,
    });
    await open(s);
    await pick(ofTask(9103), 'the worktree row');
    await until(() => cards().find((c) => c.textContent?.includes('Remove bob worktree')), 'the card');
    const c = cards().find((x) => x.textContent?.includes('Remove bob worktree'))!;
    expect(button(c, 'Done')).toBeUndefined();
    expect(button(c, 'Keep it')).toBeDefined();
    expect(button(c, 'Save a copy, then remove')).toBeDefined();
    await click(button(c, 'Remove anyway'));
    await until(() => s.daemon.tasks.find(9103).state === 'done', 'the task done');
    expect(s.daemon.tasks.find(9103).evidence).toBe('2 yes');
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

  it('a row opens its item beside the list on a wide window; its source chip is the one way to the task', async () => {
    const media = (matches: boolean) => vi.stubGlobal('matchMedia', () => ({ matches, addEventListener() {}, removeEventListener() {} }));
    media(true);
    keep = () => void vi.unstubAllGlobals();
    const s = setup();
    decision(s, 9002);
    await open(s);
    await pick(ofTask(9002), 'the decision row');
    await until(() => cards()[0], 'the item');
    expect(listRows().find(ofTask(9002))!.getAttribute('aria-current')).toBe('true');
    expect(box.querySelector('.tdetail')).toBeNull(); // no task page is embedded
    const chip = button(cards()[0]!, 'Task T9002'); // local-ref-ok
    expect(chip).toBeDefined();
    expect(cards()[0]!.textContent).not.toMatch(/View in chat|Open T9002/); // nothing trails the answers // local-ref-ok
    await click(chip);
    expect(location.hash).toBe('#/tasks/9002');
  });

  it('on a narrow window the item takes the page, with one Back to Requests', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    keep = () => void vi.unstubAllGlobals();
    const s = setup();
    decision(s, 9002);
    await open(s);
    await pick(ofTask(9002), 'the decision row');
    await until(() => cards()[0], 'the item');
    const backs = [...box.querySelectorAll('.back')];
    expect(backs.map((b) => b.getAttribute('title'))).toEqual(['Back to Requests']);
    await click(backs[0]);
    expect(cards()).toHaveLength(0);
    expect(listRows().length).toBeGreaterThan(0);
  });

  it('a decision is answered in its field, never a bare Reply', async () => {
    const s = await open();
    await pick(ofKey('item:3'), 'the decision row');
    await until(() => box.querySelector('[data-card="item:3"]'), 'the decision card');
    const c = box.querySelector('[data-card="item:3"]')!;
    expect(c.querySelector('[role=button]')).toBeNull(); // no task to open, and no clamped text to unfold
    expect(c.textContent).not.toContain('Show more');
    expect(button(c, 'Reply')).toBeUndefined(); // no bare Reply: the answer is written in its field
    await type(c.querySelector('input')!, 'Yes, relay it.');
    await click(button(c, 'Send answer'));
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
