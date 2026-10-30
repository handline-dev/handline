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
      needs_you: {
        kind: 'dialog',
        since: new Date(Date.now() - 5 * 60_000).toISOString(),
        title: 'Waiting at a dialog',
        text: 'Trust this folder?',
        source: 'hook',
      },
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
    expect(names).toContain('Open the terminal');
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
  // A button's label may end in its key hint ("ApproveA").
  const button = (c: Element, name: string) => [...c.querySelectorAll('button')].find((b) => new RegExp(`^${name}[A-Z]?$`).test(label(b)));
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
    // The answer is held for a few seconds so Undo is real: the toast says so, not that it is done. Closing the page sends it at once.
    expect(document.body.textContent).toContain('Sends in 5 s');
    expect(document.body.textContent).not.toContain('Approved');
    await act(async () => void window.dispatchEvent(new Event('pagehide')));
    await until(() => s.daemon.tasks.find(9101).state === 'done', 'the task done');
    await until(() => document.body.textContent!.includes('Approved'), 'the result once the daemon has taken it');
    expect(s.daemon.tasks.find(9101).evidence).toBe('Approved');
  });

  it('a refused held answer puts its card back and shows the daemon sentence', async () => {
    const s = setup();
    review(s, 9107);
    await open(s);
    await pick(ofTask(9107), 'the review row');
    await until(() => reviewCard(9107), 'the review card');
    await click(button(reviewCard(9107)!, 'Approve'));
    s.daemon.tasks.find(9107).state = 'discarded'; // the task closed under the held answer: the daemon will not finish it again
    await act(async () => void window.dispatchEvent(new Event('pagehide')));
    await until(() => !document.body.textContent!.includes('Sends in 5 s'), 'the hold over');
    expect(document.body.textContent).not.toContain('Approved');
    await until(() => reviewCard(9107), 'the card back');
  });

  it('an answer leaves the list at once and Undo brings it back unsent', async () => {
    const s = setup();
    review(s, 9103);
    await open(s);
    await pick(ofTask(9103), 'the review row');
    await until(() => reviewCard(9103), 'the review card');
    await click(button(reviewCard(9103)!, 'Approve'));
    expect(reviewCard(9103)).toBeUndefined();
    const undo = [...document.querySelectorAll('button')].find((b) => label(b) === 'Undo')!;
    expect(undo.closest('[role=status]')!.className).toContain('pointer-events-auto'); // the toast sits in a pointer-events-none wrapper
    await click(undo);
    await until(() => reviewCard(9103), 'the card back');
    await act(async () => void window.dispatchEvent(new Event('pagehide')));
    await wait(50);
    expect(s.daemon.tasks.find(9103).state).not.toBe('done');
  });

  it('the card that moves up into the answered one is opened next, not the top card', async () => {
    const s = setup();
    review(s, 9201);
    review(s, 9202);
    review(s, 9203);
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} })); // wide: one card is always open
    await open(s);
    await pick(ofTask(9201), 'the first review row');
    await until(() => reviewCard(9201), 'the review card');
    await click(button(reviewCard(9201)!, 'Approve'));
    await until(() => listRows().some((r) => r.getAttribute('aria-current') === 'true'), 'a card open');
    expect(
      listRows()
        .find((r) => r.getAttribute('aria-current') === 'true')!
        .getAttribute('aria-label'),
    ).toBe('Open T9202'); // local-ref-ok
  });

  it('an answer held when the page goes away is kept, and the next page sends it with the same ids', async () => {
    const s = setup();
    review(s, 9105);
    await open(s);
    await pick(ofTask(9105), 'the review row');
    await until(() => reviewCard(9105), 'the review card');
    await click(button(reviewCard(9105)!, 'Approve'));
    const left = JSON.parse(localStorage.getItem('handline:held-answers') ?? '{}') as Record<
      string,
      { method: string; params: { request_id: string } }[]
    >;
    const steps = Object.values(left)[0]!;
    expect(steps.map((x) => x.method)).toEqual(['task.done', 'inbox.mark_read']);
    expect(steps.every((x) => x.params.request_id)).toBe(true);
    // The reload: this page dies before the write leaves. Undo stands in for the lost write; the saved answer is what the next page finds.
    const saved = localStorage.getItem('handline:held-answers')!;
    await click([...document.querySelectorAll('button')].find((b) => label(b) === 'Undo'));
    const firstPage = { root, box };
    localStorage.setItem('handline:held-answers', saved);
    expect(s.daemon.tasks.find(9105).state).not.toBe('done');
    await open(s);
    await until(() => s.daemon.tasks.find(9105).state === 'done', 'the held answer sent by the next page');
    expect(localStorage.getItem('handline:held-answers')).toBe('{}');
    await act(async () => firstPage.root?.unmount());
    firstPage.box.remove();
  });

  it("a worktree-removal card has Keep it, Save a copy and Remove anyway; each closes the task with the daemon's words", async () => {
    const s = setup();
    s.daemon.tasks.seed({ id: 9103, from: ME, to: ARCH, title: 'Remove bob worktree', state: 'doing', tags: ['worktree-removal'] });
    s.daemon.inbox.add({
      kind: 'decision',
      agent: ARCH,
      title: 'Remove',
      body: 'Path: /x\nBranch: b\n\nMember: eve\n\nClose this task with the number as its answer',
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
    expect(button(c, 'Save to a branch, then remove')).toBeDefined();
    expect(c.textContent).not.toMatch(/Close this task with the number|Write an answer/);
    await click(button(c, 'Remove anyway'));
    expect(s.daemon.tasks.find(9103).state).not.toBe('done'); // the first click only asks
    await click(
      button(
        cards().find((x) => x.textContent?.includes('Remove bob worktree'))!,
        'Remove',
      ),
    );
    await until(() => s.daemon.tasks.find(9103).state === 'done', 'the task done');
    expect(s.daemon.tasks.find(9103).evidence).toBe('2 yes');
    expect(document.body.textContent).toContain("eve's worktree removed with its files");
  });

  it('a refused worktree answer says nothing was removed and the item stays', async () => {
    const s = setup();
    s.daemon.tasks.seed({ id: 9104, from: ME, to: ARCH, title: 'Remove ann worktree', state: 'doing', tags: ['worktree-removal'] });
    s.daemon.inbox.add({
      kind: 'decision',
      agent: ARCH,
      title: 'Remove',
      body: 'Path: /x\n\nMember: ann',
      task: 9104,
      created: new Date().toISOString(),
      read: false,
    });
    s.daemon.knobs.refuse('task.done', 'state', 'not a worktree git knows');
    await open(s);
    await pick(ofTask(9104), 'the worktree row');
    const find = () => cards().find((x) => x.textContent?.includes('Remove ann worktree'));
    await until(find, 'the card');
    await click(button(find()!, 'Keep it'));
    await until(() => document.body.textContent?.includes('not a worktree git knows'), 'the refusal');
    expect(document.body.textContent).not.toContain("ann's worktree stays");
    expect(s.daemon.tasks.find(9104).state).not.toBe('done');
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

  it('j and k walk the requests and Enter opens the open one', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
    keep = () => void vi.unstubAllGlobals();
    const s = setup();
    decision(s, 9002);
    decision(s, 9003);
    await open(s);
    await pick(ofTask(9002), 'the decision row');
    const key = (k: string) => act(async () => void document.body.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true })));
    const current = () =>
      listRows()
        .find((r) => r.getAttribute('aria-current') === 'true')
        ?.getAttribute('aria-label');
    await key('j');
    expect(current()).not.toBe('Open T9002'); // local-ref-ok
    await key('k');
    expect(current()).toBe('Open T9002'); // local-ref-ok
    await key('Enter');
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
    await act(async () => void window.dispatchEvent(new Event('pagehide')));
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
