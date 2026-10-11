// X5, the phone's Worktrees page on the fake daemon: the cards from the same fixtures as the web, Move to… (a decided write that waits for
// the refetch and is put back with the daemon's reason), one worktree as a page, and who may move one.
import { describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { $, box, mount, press, until } from './harness.tsx';

const text = (el: Element | null) => el?.textContent ?? '';

const AUDITOR = '/work/handline.auditor'; // todo
const OTHER = '/work/docs-site.writer'; // blocked
const open = async (m: Awaited<ReturnType<typeof mount>>) => {
  await press($('worktrees-open'));
  await until(() => $(`wt-${AUDITOR}`), 'the cards');
  return m;
};
const status = (path: string) => text($(`wt-${path}`)?.querySelector('[data-testid="wt-status"]') ?? null);
const sets = (spy: ReturnType<typeof vi.spyOn>) => spy.mock.calls.filter((c) => c[0] === 'worktree.set_status').map((c) => c[1]);
const calls = (m: Awaited<ReturnType<typeof mount>>) => vi.spyOn(m.daemon.work, 'call');

describe('X5 phone Worktrees', () => {
  it('draws one card per worktree with its status chip, owner and files', async () => {
    await open(await mount());
    expect([...box.querySelectorAll('[data-testid^="wt-/"]')].map((e) => e.getAttribute('data-testid'))).toHaveLength(5);
    expect(status(AUDITOR)).toBe('todo');
    expect(status('/work/handline.term-build')).toBe('in progress');
    expect(status('/work/handline.architect')).toBe('in review');
    expect(status('/work/spare')).toBe('done');
    expect(status(OTHER)).toBe('blocked');
    expect(text($('wt-/work/handline.term-build'))).toContain('3 files +126 −31');
    expect(text($('wt-/work/spare'))).toContain('no owner');
  });

  it('searches by branch or path', async () => {
    await open(await mount());
    const el = $('wt-search') as unknown as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, 'spare');
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect($(`wt-${AUDITOR}`)).toBeNull();
    expect($('wt-/work/spare')).not.toBeNull();
  });

  it('moves a status once, keeps the old chip with a pending mark until the refetch, then shows the new one', async () => {
    const m = await open(await mount());
    const spy = calls(m);
    const release = m.daemon.knobs.hold('worktree.set_status');
    await press($(`wt-move-${AUDITOR}`));
    expect([...box.querySelectorAll('[data-testid^="wt-to-"]')].map((e) => text(e))).toEqual(['in progress', 'in review', 'done']);
    await press($('wt-to-in review'));
    await until(() => text($(`wt-${AUDITOR}`)).includes('moving to in review…'), 'the pending mark');
    expect(status(AUDITOR)).toBe('todo');
    expect($(`wt-move-${AUDITOR}`)).toBeNull(); // nothing to tap while it moves
    release();
    await until(() => status(AUDITOR) === 'in review', 'the new status');
    expect(text($(`wt-${AUDITOR}`))).not.toContain('moving');
    expect(sets(spy)).toEqual([{ path: AUDITOR, status: 'in review', request_id: expect.any(String) }]);
    m.store.stop();
  });

  it('rolls back with the daemon’s reason when a move is refused', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('worktree.set_status', 'state', 'the label is locked'));
    await open(m);
    await press($(`wt-move-${AUDITOR}`));
    await press($('wt-to-done'));
    await until(() => $('wt-error'), 'the reason');
    expect(text($(`wt-${AUDITOR}`))).toContain('the label is locked');
    expect(status(AUDITOR)).toBe('todo');
    expect(text($(`wt-${AUDITOR}`))).not.toContain('moving');
    m.store.stop();
  });

  it('opens one worktree as a page, moves it from there, and goes back to the cards', async () => {
    const m = await open(await mount());
    await press($(`wt-open-${AUDITOR}`));
    await until(() => $('wt-detail'), 'the page');
    expect(text($('wt-detail'))).toContain(AUDITOR);
    expect(text($('wt-detail'))).toContain('No changed files.');
    await press($('wt-move'));
    await press($('wt-to-done'));
    await until(() => text($('wt-detail')).includes('done'), 'the new status');
    await press($('wt-back'));
    await until(() => $(`wt-${AUDITOR}`), 'the cards');
    expect(status(AUDITOR)).toBe('done');
    m.store.stop();
  });

  it('lists the changed files, and Open agent leaves for the agent', async () => {
    const m = await open(await mount());
    await press($('wt-open-/work/handline.term-build'));
    await until(() => $('wt-detail'), 'the page');
    expect(text($('wt-detail'))).toContain('Changed files (3 files +126 −31)');
    await press($('wt-agent'));
    await until(() => $('back'), 'the agent page');
    expect($('worktrees')).toBeNull();
    m.store.stop();
  });

  it('lets a member move a status', async () => {
    const m = await open(await mount(() => {}, undefined, undefined, { role: 'member' }));
    await press($(`wt-move-${AUDITOR}`));
    await press($('wt-to-in progress'));
    await until(() => status(AUDITOR) === 'in progress', 'the new status');
    m.store.stop();
  });

  it('draws no Move to… when set_status is not listed', async () => {
    const m = await open(await mount((s) => s.daemon.knobs.unserve('worktree.set_status')));
    expect($(`wt-move-${AUDITOR}`)).toBeNull();
    m.store.stop();
  });
});
