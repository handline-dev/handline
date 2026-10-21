// A task's history on the phone's task page, on the fake daemon: a row per field with its before and after, memo changes folded under All,
// the chips, a long text opening to its full before and after, and no control on a daemon without task.history.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { $, box, mount, press, text, until } from '#test/harness.tsx';

const TASK = 1684;
const rows = (field?: string) =>
  [...box.querySelectorAll<HTMLElement>('[data-testid^="history-"]')].filter(
    (e) => /^history-(?!filter|order|fold|full)/.test(e.dataset.testid!) && (!field || e.dataset.testid === `history-${field}`),
  );

// The fixture history reaches back some hours from now; at noon it stays on one local day, so no midnight splits a fold.
beforeEach(() => void vi.useFakeTimers({ toFake: ['Date'], now: new Date(2026, 9, 20, 12, 0) }));
afterEach(() => void vi.useRealTimers());

async function open(prepare?: Parameters<typeof mount>[0]) {
  const m = await mount(prepare);
  await press($('nav-tasks'));
  await until(() => $(`task-${TASK}`), 'the task row');
  await press($(`task-${TASK}`));
  await until(() => $('task-detail'), 'the detail');
  return m;
}
async function openHistory() {
  expect($('task-history')).toBeNull(); // not read until asked for
  await press($('task-history-toggle'));
  await until(() => $('task-history'), 'the history');
}

describe('task history', () => {
  it('shows a row per field with its before and after, memo changes folded', async () => {
    const m = await open();
    await openHistory();
    expect(text($('history-filter-all'))).toBe('All 11');
    const priority = $('history-priority')!;
    expect(text(priority)).toContain('founder');
    expect(text(priority)).toContain('P2');
    expect(text(priority)).toContain('P1');
    expect(rows('memo')).toHaveLength(0);
    expect(text($('history-fold'))).toContain('3 comment changes by');
    expect(text($('task-history'))).toContain('auditor PASS on 3f2a9c1');
    m.store.stop();
  }, 20000);

  it('filters by chip, and Comment shows what each memo said', async () => {
    const m = await open();
    await openHistory();
    await press($('history-filter-state'));
    expect(rows().every((r) => r.dataset.testid === 'history-state')).toBe(true);
    expect(rows()).toHaveLength(3);
    await press($('history-filter-fields'));
    expect(rows('state')).toHaveLength(0);
    expect(rows('memo')).toHaveLength(0);
    await press($('history-filter-memo'));
    const memos = rows('memo');
    expect(memos).toHaveLength(3);
    expect(text(memos[0])).toContain('deleted');
    expect(text(memos[0])).toContain('Skip the gap list for now.');
    expect(text(memos[1])).toContain('Chat first, then tasks.');
    expect(text(memos[2])).toContain('added');
    m.store.stop();
  }, 20000);

  it('opens a long text to its full before and after, and a fold in place', async () => {
    const m = await open();
    await openHistory();
    const body = $('history-body')!;
    expect(text(body)).toContain('Plan the web UI.');
    expect(body.querySelector('[data-testid="history-full"]')).toBeNull();
    await press(body);
    expect(text(body.querySelector('[data-testid="history-full"]'))).toContain('Three units, chat first.');
    await press($('history-fold'));
    expect(rows('memo')).toHaveLength(3);
    m.store.stop();
  }, 20000);

  it('orders oldest first on request', async () => {
    const m = await open();
    await openHistory();
    const first = () => rows()[0]!.dataset.testid;
    const newest = first();
    await press($('history-order'));
    expect(first()).not.toBe(newest);
    m.store.stop();
  }, 20000);

  it('shows no control on a daemon without task.history', async () => {
    const m = await open((s) => s.daemon.knobs.unserve('task.history'));
    expect($('task-history-toggle')).toBeNull();
    m.store.stop();
  }, 20000);
});
