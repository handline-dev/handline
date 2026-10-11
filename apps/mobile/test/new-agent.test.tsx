// X5, the phone's New agent page on the fake daemon's agent.create: the form, one call per tap, the fields it sends, the progress, done and
// failed, the first task, who sees the entry, and the return to the progress after a reload.
import { act, useState, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { Store } from '@handline/app-core';
import { Home } from '../src/screens/Home.tsx';
import type { Pending } from '../src/screens/NewAgent.tsx';
import { $, box, mount, press, until } from './harness.tsx';

const text = (el: Element | null) => el?.textContent ?? '';
const type = (id: string, value: string) =>
  act(async () => {
    const el = $(id) as unknown as HTMLInputElement | HTMLTextAreaElement;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const calls = (m: Awaited<ReturnType<typeof mount>>) => vi.spyOn(m.daemon.work, 'call');
const made = (spy: ReturnType<typeof calls>) => spy.mock.calls.filter((c) => c[0] === 'agent.create').map((c) => c[1]);
/** react-native-web draws no aria-selected: a chosen chip is the bold one. */
const chosen = (id: string) => ($(id)?.firstElementChild as HTMLElement | null)?.style.fontWeight === '700';
const create = () => $('na-create') as unknown as HTMLButtonElement;
const off = () => create().getAttribute('aria-disabled') === 'true' || create().hasAttribute('disabled');

async function open(m: Awaited<ReturnType<typeof mount>>) {
  await until(() => $('new-agent-open'), 'the entry');
  await press($('new-agent-open'));
  await until(() => $('na-name'), 'the page');
  return m;
}
/** The form with name, role and docs-site (whose default branch is master). */
async function fill(name = 'fresh', role = 'reviewer') {
  await until(() => $('na-repo-/work/docs-site'), 'the repos');
  await type('na-name', name);
  await type('na-role', role);
  await press($('na-repo-/work/docs-site'));
}

describe('X5 phone New agent', () => {
  it('keeps Create off while name, role or repo is empty, and sends nothing', async () => {
    const m = await open(await mount());
    const spy = calls(m);
    expect(off()).toBe(true);
    await type('na-name', 'fresh');
    await type('na-role', 'reviewer');
    expect(off()).toBe(true); // no repo yet
    await until(() => $('na-repo-/work/docs-site'), 'the repos');
    await press($('na-repo-/work/docs-site'));
    expect(off()).toBe(false);
    await type('na-name', '');
    expect(off()).toBe(true);
    await press(create());
    expect(made(spy)).toEqual([]);
    m.store.stop();
  });

  it('sends one call per tap with the repo path and its default branch, and leaves model, effort and parent out at their defaults', async () => {
    const m = await open(await mount());
    const spy = calls(m);
    await fill();
    expect(($('na-base') as unknown as HTMLInputElement).value).toBe('master');
    const release = m.daemon.knobs.hold('agent.create');
    await press(create());
    await until(() => off(), 'Create off while the call waits');
    await press(create());
    release();
    await until(() => $('na-progress') || $('na-done'), 'the progress');
    expect(made(spy)).toHaveLength(1);
    expect(made(spy)[0]).toMatchObject({ name: 'fresh', role: 'reviewer', repo: '/work/docs-site', base: 'master' });
    for (const k of ['model', 'effort', 'parent', 'account']) expect(made(spy)[0]).not.toHaveProperty(k);
    m.store.stop();
  });

  it('offers five efforts after default, and sends model and effort when chosen', async () => {
    const m = await open(await mount());
    const spy = calls(m);
    await fill();
    expect([...box.querySelectorAll('[data-testid^="na-effort-"]')].map((e) => e.getAttribute('data-testid'))).toEqual(
      ['default', 'low', 'medium', 'high', 'xhigh', 'max'].map((e) => `na-effort-${e}`),
    );
    await press($('na-effort-high'));
    await type('na-model', 'opus');
    await press(create());
    await until(() => made(spy).length === 1, 'the call');
    expect(made(spy)[0]).toMatchObject({ model: 'opus', effort: 'high' });
    m.store.stop();
  });

  it('draws the account note and never sends an account', async () => {
    const m = await open(await mount());
    expect(text($('new-agent'))).toContain('chosen when it starts');
    m.store.stop();
  });

  it('prefixes the name with the parent and sends the whole name', async () => {
    const m = await open(await mount());
    const spy = calls(m);
    await fill('kid');
    await press($('na-parent-handline.architect'));
    expect(text($('new-agent'))).toContain('after handline.architect.');
    await press(create());
    await until(() => made(spy).length === 1, 'the call');
    expect(made(spy)[0]).toMatchObject({ name: 'handline.architect.kid', parent: 'handline.architect' });
    m.store.stop();
  });

  it('shows the steps the daemon sent and no others, counts them, then offers Open agent when done', async () => {
    const m = await mount((s) => (s.daemon.work.stepMs = 120));
    await open(m);
    await fill();
    await press(create());
    const seen = new Set<string>();
    const note = () => {
      const t = text($('na-progress'));
      for (const step of ['registering the agent', 'making its worktree', 'starting the agent']) if (t.includes(step)) seen.add(step);
      if (/step [123] of 3/.test(t)) seen.add('count');
    };
    const watch = new MutationObserver(note);
    watch.observe(document.body, { subtree: true, childList: true, characterData: true });
    note();
    try {
      await until(() => $('na-done'), 'done', 3000);
    } finally {
      watch.disconnect();
    }
    expect([...seen]).toEqual(['registering the agent', 'count', 'making its worktree', 'starting the agent']);
    expect(text($('na-done'))).toContain('fresh is running');
    await press($('na-open'));
    await until(() => $('back'), 'the agent page');
    expect($('new-agent')).toBeNull();
    m.store.stop();
  });

  it('returns to the form with its fields kept when the operation fails, and says what the daemon said', async () => {
    const m = await open(await mount());
    await fill();
    await type('na-base', 'nope');
    await press(create());
    await until(() => $('na-failed'), 'the failure');
    expect(text($('na-failed'))).toContain('no branch nope in docs-site');
    expect(text($('na-failed'))).toContain('fresh is registered, not started.');
    await press($('na-edit'));
    await until(() => $('na-name'), 'the form');
    expect(($('na-name') as unknown as HTMLInputElement).value).toBe('fresh');
    expect(($('na-role') as unknown as HTMLInputElement).value).toBe('reviewer');
    expect(($('na-base') as unknown as HTMLInputElement).value).toBe('nope');
    expect(chosen('na-repo-/work/docs-site')).toBe(true);
    m.store.stop();
  });

  it('keeps the fields and shows the reason when agent.create is refused', async () => {
    const m = await open(await mount());
    await fill('handline.auditor');
    await press(create());
    await until(() => $('na-error'), 'the refusal');
    expect(text($('na-error'))).toContain('already an agent');
    expect(($('na-name') as unknown as HTMLInputElement).value).toBe('handline.auditor');
    m.store.stop();
  });

  it('sends the first task as one task.new with a time box after done', async () => {
    const m = await open(await mount());
    const spy = vi.spyOn(m.client, 'call');
    await fill();
    await type('na-task', 'Read the docs\nthen report');
    await type('na-box', '3h');
    await press(create());
    await until(() => spy.mock.calls.some((c) => c[0] === 'task.new'), 'the task');
    const asks = spy.mock.calls.filter((c) => c[0] === 'task.new');
    expect(asks).toHaveLength(1);
    expect(asks[0]![1]).toMatchObject({ owner: 'fresh', title: 'Read the docs', body: 'then report', time_box: '3h' });
    m.store.stop();
  });

  it('keeps the first task text with a Retry when task.new is refused', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('task.new', 'state', 'the task board is closed'));
    await open(m);
    await fill();
    await type('na-task', 'Read the docs');
    await press(create());
    await until(() => text($('na-done')).includes('the first task was not sent'), 'the note');
    expect(text($('na-done'))).toContain('the task board is closed');
    expect(($('na-retask') as unknown as HTMLTextAreaElement).value).toBe('Read the docs');
    expect($('na-retry-task')).not.toBeNull();
    m.store.stop();
  });

  it('clears the form for Create another', async () => {
    const m = await open(await mount());
    await fill();
    await press(create());
    await until(() => $('na-done'), 'done');
    await press($('na-another'));
    await until(() => $('na-name'), 'the form');
    expect(($('na-name') as unknown as HTMLInputElement).value).toBe('');
    expect(chosen('na-repo-/work/docs-site')).toBe(false);
    m.store.stop();
  });

  it('has no entry for a member', async () => {
    const m = await mount(() => {}, undefined, undefined, { role: 'member' });
    await until(() => $('worktrees-open'), 'the list');
    expect($('new-agent-open')).toBeNull();
    m.store.stop();
  });

  it('has no entry when the daemon does not list agent.create', async () => {
    const m = await mount((s) => s.daemon.knobs.unserve('agent.create'));
    await until(() => $('worktrees-open'), 'the list');
    expect($('new-agent-open')).toBeNull();
    m.store.stop();
  });

  it('shows the error with Retry when the operation cannot be read', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('operation.get', 'state', 'the operation store is down'), undefined, undefined, {
      screen: (store) => <Reload store={store} pending={{ operation: 'op-9', save: () => {} }} />,
    });
    await until(() => $('na-error'), 'the error');
    expect(text($('na-error'))).toContain('the operation store is down');
    m.store.stop();
  });

  it('returns to the progress page after a reload while the operation runs, and not after it is closed', async () => {
    let kept: string | null = null;
    const pending = (): Pending => ({ operation: kept, save: (op) => void (kept = op) });
    let reload: () => void = () => {};
    const screen = (store: Store): ReactNode => <Reload store={store} pending={pending} expose={(r) => (reload = r)} />;
    const m = await mount((s) => (s.daemon.work.stepMs = 400), undefined, undefined, { screen });
    await open(m);
    await fill();
    await press(create());
    await until(() => $('na-progress'), 'the progress');
    expect(kept).toMatch(/\S/);
    await act(async () => reload());
    await until(() => $('na-progress') || $('na-done'), 'the progress after the reload');
    expect($('na-name')).toBeNull();
    await until(() => $('na-done'), 'done', 3000);
    await press($('na-back'));
    expect(kept).toBeNull();
    await act(async () => reload());
    expect($('new-agent')).toBeNull();
    m.store.stop();
  });
});

/** Home as the app mounts it, remountable to stand for a relaunch: the pending page is kept outside, as the phone keeps it. */
function Reload(props: { store: Store; pending: Pending | (() => Pending); expose?: (reload: () => void) => void }) {
  const [n, setN] = useState(0);
  props.expose?.(() => setN((x) => x + 1));
  return (
    <Home key={n} store={props.store} onUnpair={() => {}} pending={typeof props.pending === 'function' ? props.pending() : props.pending} />
  );
}
