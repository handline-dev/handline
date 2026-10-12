// X4: the Worktrees board and the status writes: five columns from the fixtures, a drop that calls once and moves the card only after the
// refetch, a refusal that leaves it with the reason, a member who may change a status, the key `m`, and the select in the panel.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

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
  const data = new Map<string, string>([
    ['rail', '1'],
    ['terminal', 'text'],
  ]);
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

const AUDITOR = '/work/handline.auditor'; // todo
let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const card = (path: string) => $(`.wt-card[data-worktree="${path}"]`)!;
const column = (status: string) => $(`.wt-col[data-status="${status}"]`)!;
const inColumn = (status: string) => $$(`.wt-col[data-status="${status}"] .wt-card`).map((c) => c.dataset.worktree);
const fire = (el: Element, type: string, init: object = {}) =>
  act(async () => void el.dispatchEvent(Object.assign(new Event(type, { bubbles: true }), init)));
const drag = async (path: string, status: string) => {
  await fire(card(path), 'dragstart');
  await fire(column(status), 'drop');
};
const calls = (s: ReturnType<typeof setup>) => vi.spyOn(s.daemon.work, 'call');
const sets = (spy: ReturnType<typeof calls>) => spy.mock.calls.filter((c) => c[0] === 'worktree.set_status').map((c) => c[1]);

async function mount(s = setup(), hash = '#/worktrees') {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => $$('.wt-table tbody tr').length === 5, 'the rows');
  return s;
}
const board = async () => {
  await click($('[role=radio][aria-label="Board"]'));
  await until(() => $('.wt-board'), 'the board');
};

afterEach(async () => {
  vi.restoreAllMocks();
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('X4 worktree board', () => {
  it('draws the four columns and an other column from the fixtures, each card in its status', async () => {
    await mount();
    await board();
    expect($$('.wt-col').map((c) => c.dataset.status)).toEqual(['todo', 'in progress', 'in review', 'done', 'other']);
    expect(inColumn('todo')).toEqual([AUDITOR]);
    expect(inColumn('in progress')).toEqual(['/work/handline.term-build']);
    expect(inColumn('in review')).toEqual(['/work/handline.architect']);
    expect(inColumn('done')).toEqual(['/work/spare']);
    expect(inColumn('other')).toEqual(['/work/docs-site.writer']);
    expect(text(card('/work/handline.term-build'))).toContain('3 files +126 -31');
  });

  it('calls once per drop, keeps the card where it was until the refetch shows the new status, then moves it', async () => {
    const s = setup();
    const spy = calls(s);
    await mount(s);
    await board();
    const release = s.daemon.knobs.hold('worktree.set_status');
    await drag(AUDITOR, 'in review');
    await until(() => text(card(AUDITOR)).includes('moving to in review…'), 'the pending mark');
    expect(inColumn('todo')).toEqual([AUDITOR]);
    await drag(AUDITOR, 'done'); // a second drop on a card that is moving sends nothing
    release();
    await until(() => inColumn('in review').includes(AUDITOR), 'the card to move');
    expect(inColumn('todo')).toEqual([]);
    expect(text(card(AUDITOR))).not.toContain('moving');
    expect(sets(spy)).toEqual([{ path: AUDITOR, status: 'in review', request_id: expect.any(String) }]);
  });

  it('leaves the card in place with the daemon’s reason when a move is refused', async () => {
    const s = setup();
    s.daemon.knobs.refuse('worktree.set_status', 'state', 'the label is locked');
    await mount(s);
    await board();
    await drag(AUDITOR, 'done');
    await until(() => $('.wt-card .wt-error'), 'the reason');
    expect(text(card(AUDITOR))).toContain('the label is locked');
    expect(inColumn('todo')).toEqual([AUDITOR]);
    expect(text(card(AUDITOR))).not.toContain('moving');
  });

  it('lets a member change a status', async () => {
    const s = setup({ role: 'member' });
    await mount(s);
    await board();
    await drag(AUDITOR, 'in progress');
    await until(() => inColumn('in progress').includes(AUDITOR), 'the card to move');
  });

  it('opens Move to… with the key m on a focused card and does what a drop does', async () => {
    const s = setup();
    const spy = calls(s);
    await mount(s);
    await board();
    await fire(card(AUDITOR), 'keydown', { key: 'm' });
    const menu = $('.wt-menu')!;
    expect([...menu.querySelectorAll('[role=menuitem]')].map((b) => text(b))).toEqual(['in progress', 'in review', 'done']);
    await click([...menu.querySelectorAll('button')].find((b) => text(b) === 'in review'));
    await until(() => inColumn('in review').includes(AUDITOR), 'the card to move');
    expect(sets(spy)).toHaveLength(1);
    expect($('.wt-menu')).toBeNull();
  });

  it('opens the Move to… menu from the button too', async () => {
    await mount();
    await board();
    await click([...card(AUDITOR).querySelectorAll('button')].find((b) => text(b) === 'Move to…'));
    expect($('.wt-menu')).not.toBeNull();
  });

  it('changes the status from the select in the panel, in the list view too', async () => {
    const s = setup();
    const spy = calls(s);
    await mount(s, `#/worktrees/${encodeURIComponent(AUDITOR)}`);
    await until(() => $('.wt-panel select[aria-label="Status"]'), 'the select');
    const sel = $('.wt-panel select[aria-label="Status"]') as HTMLSelectElement;
    expect([...sel.options].map((o) => o.value)).toEqual(['todo', 'in progress', 'in review', 'done']);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(sel, 'done');
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await until(() => text($$('.wt-table tbody tr').find((r) => r.dataset.worktree === AUDITOR)).includes('done'), 'the row');
    expect(sets(spy)).toHaveLength(1);
  });

  it('offers a status outside the four in the panel select, and no controls when set_status is not listed', async () => {
    const s = setup();
    await mount(s, `#/worktrees/${encodeURIComponent('/work/docs-site.writer')}`);
    await until(() => $('.wt-panel select[aria-label="Status"]'), 'the select');
    expect([...($('.wt-panel select[aria-label="Status"]') as HTMLSelectElement).options].map((o) => o.value)[0]).toBe('blocked');
    await act(async () => root?.unmount());
    box.remove();
    const t = setup();
    t.daemon.knobs.unserve('worktree.set_status');
    await mount(t, `#/worktrees/${encodeURIComponent(AUDITOR)}`);
    await until(() => $('.wt-panel'), 'the panel');
    expect($('.wt-panel select[aria-label="Status"]')).toBeNull();
    await board();
    expect($('.wt-card button[aria-haspopup]')).toBeNull();
  });
});
