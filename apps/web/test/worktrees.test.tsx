// X1: the Worktrees page on the fake daemon's repos and worktrees: skeleton, rows, "no owner", the filters and the search, the side panel with
// its files and Open agent, the empty, filtered-empty and not-served states, and the note that the snapshot is old.
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

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const row = (path: string) => $(`[data-worktree="${path}"]`)!;
const rows = () => $$('.wt-table tbody tr');
const pick = (label: string, value: string) =>
  act(async () => {
    const s = $(`select[aria-label="${label}"]`) as HTMLSelectElement;
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(s, value);
    s.dispatchEvent(new Event('change', { bubbles: true }));
  });
const type = (el: HTMLElement, v: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

async function mount(hash = '#/worktrees', s = setup()) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  return s;
}
afterEach(async () => {
  vi.useRealTimers();
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('X1 Worktrees', () => {
  it('shows eight skeleton rows while loading, then the fixtures as rows', async () => {
    const s = setup();
    s.daemon.knobs.latency(400);
    await mount('#/worktrees', s);
    await until(() => $('.worktrees .skeleton'), 'the skeleton');
    expect($('.worktrees .skeleton')!.getAttribute('aria-busy')).toBe('true');
    expect($$('.worktrees .skeleton > *')).toHaveLength(8);
    await until(() => rows().length === 5, 'the rows', 600);
    expect($('.worktrees .skeleton')).toBeNull();
    expect(text(row('/work/handline.term-build'))).toContain('buddyron/handline.term-build');
    expect(text(row('/work/handline.term-build'))).toContain('in progress');
    expect(text(row('/work/handline.term-build'))).toContain('3 +126 -31');
    expect(text(row('/work/handline.term-build'))).toContain('handline.term-build');
  });

  it('reads a null owner as "no owner"', async () => {
    await mount();
    await until(() => rows().length === 5, 'the rows');
    expect(text(row('/work/spare'))).toContain('no owner');
    expect(row('/work/spare').querySelector('button.link[class]')?.textContent).not.toBe('no owner');
  });

  it('narrows by repo, by owner (and by no owner) and by search, and clears from the empty result', async () => {
    await mount();
    await until(() => rows().length === 5, 'the rows');
    await pick('Repo', 'docs-site');
    expect(rows().map((r) => r.dataset.worktree)).toEqual(['/work/docs-site.writer']);
    await pick('Repo', '');
    await pick('Owner', 'handline.architect');
    expect(rows().map((r) => r.dataset.worktree)).toEqual(['/work/handline.architect']);
    await pick('Owner', '\u0000none');
    expect(rows()).toHaveLength(2);
    await pick('Owner', '');
    await type($('input[aria-label="Search worktrees"]')!, 'spare');
    expect(rows().map((r) => r.dataset.worktree)).toEqual(['/work/spare']);
    await type($('input[aria-label="Search worktrees"]')!, 'zzz');
    expect(text($('.wt-none'))).toContain('No worktree matches');
    await click($$('button').find((b) => text(b) === 'Clear filters'));
    expect(rows()).toHaveLength(5);
  });

  it('opens a side panel with the changed files and Open agent lands on the workspace', async () => {
    await mount();
    await until(() => rows().length === 5, 'the rows');
    await click(row('/work/handline.term-build').querySelector('button.link'));
    expect(location.hash).toBe(`#/worktrees/${encodeURIComponent('/work/handline.term-build')}`);
    await until(() => $('.wt-panel'), 'the panel');
    const files = $$('.wt-files tbody tr').map((r) => text(r));
    expect(files).toEqual(['Aapps/web/src/Worktrees.tsx+120-0', 'Mapps/web/src/route.ts+6-1', 'Ddocs/old.md+0-30']);
    expect(text($('.wt-panel'))).toContain('handline.architect');
    expect($$('.wt-panel [role=tab]')).toHaveLength(0); // no Changes tab while capabilities.review is false
    await click($$('.wt-panel button').find((b) => text(b) === 'Open agent'));
    expect(location.hash).toBe('#/agents/handline.term-build');
    await until(() => !$('.worktrees'), 'the workspace');
    expect($('.worktrees')).toBeNull();
  });

  it('opens straight to a worktree from its URL, and says so for a path that is not one', async () => {
    await mount(`#/worktrees/${encodeURIComponent('/work/spare')}`);
    await until(() => $('.wt-panel'), 'the panel');
    expect(text($('.wt-panel'))).toContain('spare/old-idea');
    expect(text($('.wt-panel'))).toContain('No changed files.');
    expect($$('.wt-panel button').some((b) => text(b) === 'Open agent')).toBe(false);
    await act(async () => void (location.hash = `#/worktrees/${encodeURIComponent('/work/gone')}`));
    await until(() => text($('.wt-panel')).includes('No worktree at /work/gone.'), 'the missing note');
  });

  it('says there are none, with an empty daemon list', async () => {
    const s = setup();
    s.daemon.admin.worktrees.rows = [];
    await mount('#/worktrees', s);
    await until(() => text($('.worktrees')).includes('No worktrees yet.'), 'the empty state');
  });

  it('shows the refusal with Retry and not "No worktrees yet." when the list is refused', async () => {
    const s = setup();
    s.daemon.knobs.refuse('worktrees.list', 'state', 'the worktree scan failed');
    await mount('#/worktrees', s);
    await until(() => $('.worktrees .banner'), 'the banner');
    expect(text($('.worktrees .banner'))).toContain('the worktree scan failed');
    expect(text($('.worktrees .banner'))).toContain('Retry');
    expect(text($('.worktrees'))).not.toContain('No worktrees yet.');
    expect(rows()).toHaveLength(0);
  });

  it('says so when the daemon does not serve worktrees, and has no rail entry', async () => {
    await mount('#/worktrees', setup({ daemon: { admin: false } }));
    await until(() => text($('.worktrees')).includes('This daemon does not serve worktrees.'), 'the note');
    expect($$('.rail-nav button').some((b) => text(b).includes('Worktrees'))).toBe(false);
  });

  it('has a rail entry when served, and it opens the page', async () => {
    await mount('#/inbox');
    await until(() => $$('.rail-nav button').some((b) => text(b).includes('Worktrees')), 'the entry');
    await click($$('.rail-nav button').find((b) => text(b).includes('Worktrees')));
    expect(location.hash).toBe('#/worktrees');
    await until(() => rows().length === 5, 'the rows');
  });

  it('notes how old the snapshot is past 30 s, with a Refresh that reads again', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    const s = await mount();
    await until(() => rows().length === 5, 'the rows');
    expect($('.wt-old')).toBeNull();
    s.daemon.admin.worktrees.rows = s.daemon.admin.worktrees.rows.slice(0, 2);
    await act(async () => {
      vi.setSystemTime(Date.now() + 40_000);
      vi.advanceTimersByTime(5000);
    });
    expect(text($('.wt-old'))).toMatch(/refreshed 4\ds ago/);
    await click($$('.wt-old button')[0]);
    await until(() => rows().length === 2, 'the fresh rows');
    expect($('.wt-old')).toBeNull();
  });

  it('shows the refusal and not "No worktree at …" for a deep link when the list is refused', async () => {
    const s = setup();
    s.daemon.knobs.refuse('worktrees.list', 'state', 'the worktree scan failed');
    await mount(`#/worktrees/${encodeURIComponent('/work/spare')}`, s);
    await until(() => $('.worktrees .banner'), 'the banner');
    expect(text($('.worktrees .banner'))).toContain('the worktree scan failed');
    expect($('.wt-panel')).toBeNull();
    expect(text($('.worktrees'))).not.toContain('No worktree at');
  });
});
