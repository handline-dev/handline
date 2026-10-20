// The change history of a task: one row per field, fields of one edit under one time, memo changes folded under All, the chips, a long text opening to its full diff.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store } from '@handline/app-core';
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
let root: Root | null = null;
let box: HTMLElement;
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const chip = (word: string) =>
  [...box.querySelectorAll<HTMLButtonElement>('.thistory [aria-pressed]')].find((b) => b.textContent!.startsWith(word))!;
const rows = () => [...box.querySelectorAll<HTMLElement>('.thistory [data-field]')];

// The fixture history reaches back some hours from now; at noon it stays on one local day, so no midnight splits a fold.
beforeEach(() => void vi.useFakeTimers({ toFake: ['Date'], now: new Date(2026, 9, 20, 12, 0) }));
async function mount() {
  const s = setup();
  location.hash = '#/tasks/1684';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  await until(() => box.querySelector('.tverbs'), 'the actions');
  expect(box.querySelector('.thistory')).toBeNull(); // folded away until asked for
  await click([...box.querySelectorAll<HTMLElement>('.tverbs button')].find((b) => b.textContent!.startsWith('More'))!);
  await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => i.textContent === 'Show history')!);
  await until(() => box.querySelector('.thistory'), 'the history');
  return s;
}
afterEach(async () => {
  vi.useRealTimers();
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('task history', () => {
  it('shows a row per field with its before struck and its after bold, memo changes folded', async () => {
    await mount();
    expect([...box.querySelectorAll('h3')].find((h) => h.textContent!.includes('History'))?.textContent).toMatch(/History.*11 changes/);
    const priority = rows().find((r) => r.dataset.field === 'priority')!;
    expect(priority.textContent).toContain('founder');
    expect(priority.querySelector('.line-through')?.textContent).toBe('P2');
    expect(priority.querySelector('.font-semibold')?.textContent).toBe('P1');
    expect(rows().some((r) => r.dataset.field === 'memo')).toBe(false);
    expect(box.querySelector('[data-fold]')?.textContent).toContain('3 comment changes by');
    const state = rows().find((r) => r.dataset.field === 'state' && r.textContent!.includes('done'))!;
    expect(state.querySelector('[data-state="done"]')).not.toBeNull();
    expect(state.textContent).toContain('auditor PASS on 3f2a9c1');
    expect(rows().filter((r) => r.textContent!.includes('opened'))).toHaveLength(1);
  });

  it('filters by chip, and Memo shows what each memo said, edits with the old and new text', async () => {
    await mount();
    await click(chip('State'));
    expect(rows().every((r) => r.dataset.field === 'state')).toBe(true);
    expect(rows()).toHaveLength(3);
    await click(chip('Fields'));
    expect(rows().some((r) => ['state', 'memo'].includes(r.dataset.field!))).toBe(false);
    await click(chip('Comment'));
    const memos = rows();
    expect(memos.map((r) => r.querySelector('strong')?.textContent)).toEqual(['deleted', 'edited', 'added']);
    expect(memos[1]!.querySelector('.line-through')?.textContent).toBe('Chat first, then tasks.');
    expect(memos[0]!.querySelector('.text-bad')?.textContent).toBe('Skip the gap list for now.');
  });

  it('opens a long text to its full before and after, and a fold in place', async () => {
    await mount();
    const body = rows().find((r) => r.dataset.field === 'body')!;
    expect(body.textContent).toContain('Plan the web UI.');
    expect(body.querySelector('[data-full]')).toBeNull();
    expect(body.querySelector('[role="button"]')?.textContent).toMatch(/▸$/);
    await click(body.querySelector('[role="button"]')!);
    expect(body.querySelector('[data-full]')?.textContent).toContain('Three units, chat first.');
    await click(box.querySelector('[data-fold]')!);
    expect(rows().filter((r) => r.dataset.field === 'memo')).toHaveLength(3);
  });

  it('records a new edit by who made it', async () => {
    const s = await mount();
    s.daemon.tasks.verbs = true;
    await s.client.start();
    await s.client.call('task.edit', { task: 1684, title: 'Web UI v2', priority: 2, request_id: 'h1' });
    await until(() => rows().some((r) => r.dataset.field === 'title' && r.textContent!.includes('Web UI v2')), 'the new row');
    const first = rows()
      .slice(0, 2)
      .map((r) => r.dataset.field);
    expect(first).toEqual(['title', 'priority']);
  });
});
