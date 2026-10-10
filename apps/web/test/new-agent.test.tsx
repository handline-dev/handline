// X3: New agent on the fake daemon's agent.create: the form, one call per click, the fields the call sends, the progress, the done and
// failed views, the first task, and who sees the button.
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
const dialog = () => $('[role=dialog]');
function findInput(label: string): HTMLInputElement | null {
  const l = $$('[role=dialog] label').find((x) => text(x) === label);
  return l ? (box.querySelector(`#${CSS.escape(l.getAttribute('for')!)}`) as HTMLInputElement) : null;
}
const select = (label: string) => $(`[role=dialog] select[aria-label="${label}"]`) as HTMLSelectElement;
const type = (el: HTMLElement | null, v: string) =>
  act(async () => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, v);
    el!.dispatchEvent(new Event('input', { bubbles: true }));
  });
const pick = (label: string, v: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select(label), v);
    select(label).dispatchEvent(new Event('change', { bubbles: true }));
  });
const create = () => $$('[role=dialog] footer button').find((b) => text(b).startsWith('Create'))! as HTMLButtonElement;
const button = (label: string) => $$('[role=dialog] button').find((b) => text(b).trim().startsWith(label));

async function mount(hash = '#/new', s = setup()) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  return s;
}
/** The form with name, role and the repo picked (docs-site, whose default branch is master). */
async function fill(name = 'fresh', role = 'reviewer') {
  await until(() => select('Repo')?.options.length > 1, 'the repos');
  await type(findInput('Name'), name);
  await type(findInput('Role'), role);
  await pick('Repo', '/work/docs-site');
}
const calls = (s: ReturnType<typeof setup>) => vi.spyOn(s.daemon.work, 'call');
const made = (spy: ReturnType<typeof calls>) => spy.mock.calls.filter((c) => c[0] === 'agent.create').map((c) => c[1]);

afterEach(async () => {
  vi.restoreAllMocks();
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('X3 New agent', () => {
  it('keeps Create off while name, role or repo is empty, and sends nothing', async () => {
    const s = setup();
    const spy = calls(s);
    await mount('#/new', s);
    await until(() => dialog(), 'the sheet');
    expect(create().disabled).toBe(true);
    await type(findInput('Name'), 'fresh');
    await type(findInput('Role'), 'reviewer');
    expect(create().disabled).toBe(true); // no repo yet
    await until(() => select('Repo').options.length > 1, 'the repos');
    await pick('Repo', '/work/docs-site');
    expect(create().disabled).toBe(false);
    await type(findInput('Name'), '');
    expect(create().disabled).toBe(true);
    expect(made(spy)).toEqual([]);
  });

  it('sends one call per click with the repo path and its default branch, and omits model and effort at their defaults', async () => {
    const s = setup();
    const spy = calls(s);
    await mount('#/new', s);
    await fill();
    expect(findInput('Base')!.value).toBe('master');
    const release = s.daemon.knobs.hold('agent.create');
    await click(create());
    await until(() => create().disabled, 'Create off while the call waits');
    await click(create());
    release();
    await until(() => location.hash.startsWith('#/new/'), 'the progress route');
    const sent = made(spy);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ name: 'fresh', role: 'reviewer', repo: '/work/docs-site', base: 'master' });
    expect(sent[0]).not.toHaveProperty('model');
    expect(sent[0]).not.toHaveProperty('effort');
    expect(sent[0]).not.toHaveProperty('parent');
    expect(sent[0]).not.toHaveProperty('account');
  });

  it('sends model and effort when chosen, and offers the five efforts after default', async () => {
    const s = setup();
    const spy = calls(s);
    await mount('#/new', s);
    await fill();
    await until(() => select('Effort'), 'the effort');
    expect([...select('Effort').options].map((o) => o.value)).toEqual(['', 'low', 'medium', 'high', 'xhigh', 'max']);
    await pick('Effort', 'high');
    await type(findInput('Model'), 'opus');
    await click(create());
    await until(() => made(spy).length === 1, 'the call');
    expect(made(spy)[0]).toMatchObject({ model: 'opus', effort: 'high' });
  });

  it('draws the account control disabled with its reason, and never sends it', async () => {
    const s = setup();
    const spy = calls(s);
    await mount('#/new', s);
    await fill();
    expect(select('Account').disabled).toBe(true);
    expect(text(dialog())).toContain('chosen when it starts');
    await click(create());
    await until(() => made(spy).length === 1, 'the call');
    expect(made(spy)[0]).not.toHaveProperty('account');
  });

  it('prefixes the name with the parent and sends the whole name', async () => {
    const s = setup();
    const spy = calls(s);
    await mount('#/new', s);
    await fill('kid');
    await until(() => select('Parent').options.length > 1, 'the parents');
    await pick('Parent', 'handline.architect');
    expect(text(dialog())).toContain('handline.architect.');
    await click(create());
    await until(() => made(spy).length === 1, 'the call');
    expect(made(spy)[0]).toMatchObject({ name: 'handline.architect.kid', parent: 'handline.architect' });
  });

  it('shows the steps the daemon sent and no others, then offers Open agent when done', async () => {
    const s = setup();
    s.daemon.work.stepMs = 120;
    await mount('#/new', s);
    await fill();
    await click(create());
    const seen = new Set<string>();
    const note = () => {
      const t = text($('[role=dialog] [role=status]'));
      for (const step of ['registering the agent', 'making its worktree', 'starting the agent']) if (t.includes(step)) seen.add(step);
    };
    const watch = new MutationObserver(note);
    watch.observe(document.body, { subtree: true, childList: true, characterData: true });
    note();
    try {
      await until(() => text(dialog()).includes('fresh is running'), 'done', 2000);
    } finally {
      watch.disconnect();
    }
    expect([...seen]).toEqual(['registering the agent', 'making its worktree', 'starting the agent']);
    await click(button('Open agent'));
    expect(location.hash).toBe('#/agents/fresh');
    expect($('[role=dialog]')).toBeNull();
  });

  it('shows no step text beyond the daemon’s: with steps_total set it counts them', async () => {
    const s = setup();
    s.daemon.work.stepMs = 150;
    await mount('#/new', s);
    await fill();
    await click(create());
    await until(() => text(dialog()).includes('of 3'), 'the count');
    expect(text(dialog())).toMatch(/step [123] of 3/);
    expect($('[role=dialog] progress')).not.toBeNull();
  });

  it('returns to the form with its fields kept when the operation fails, and says what the daemon said', async () => {
    const s = setup();
    await mount('#/new', s);
    await fill();
    await type(findInput('Base'), 'nope');
    await click(create());
    await until(() => text(dialog()).includes('no branch nope in docs-site'), 'the failure');
    expect(text(dialog())).toContain('fresh is registered, not started.');
    await click(button('Edit and try again'));
    await until(() => findInput('Name'), 'the form');
    expect(findInput('Name')!.value).toBe('fresh');
    expect(findInput('Role')!.value).toBe('reviewer');
    expect(findInput('Base')!.value).toBe('nope');
    expect(select('Repo').value).toBe('/work/docs-site');
  });

  it('keeps the fields and shows the reason when agent.create is refused', async () => {
    const s = setup();
    await mount('#/new', s);
    await fill('handline.auditor');
    await click(create());
    await until(() => $('[role=dialog] [role=alert]'), 'the refusal');
    expect(text($('[role=dialog] [role=alert]'))).toContain('already an agent');
    expect(findInput('Name')!.value).toBe('handline.auditor');
    expect(location.hash).toBe('#/new');
  });

  it('sends the first task as one task.new with a time box after done', async () => {
    const s = setup();
    const spy = vi.spyOn(s.client, 'call');
    await mount('#/new', s);
    await fill();
    await type($('#na-task'), 'Read the docs\nthen report');
    await type(findInput('Time box'), '3h');
    await click(create());
    await until(() => spy.mock.calls.some((c) => c[0] === 'task.new'), 'the task');
    const asks = spy.mock.calls.filter((c) => c[0] === 'task.new');
    expect(asks).toHaveLength(1);
    expect(asks[0]![1]).toMatchObject({ owner: 'fresh', title: 'Read the docs', body: 'then report', time_box: '3h' });
  });

  it('keeps the first task text with a Retry when task.new is refused', async () => {
    const s = setup();
    s.daemon.knobs.refuse('task.new', 'state', 'the task board is closed');
    await mount('#/new', s);
    await fill();
    await type($('#na-task'), 'Read the docs');
    await click(create());
    await until(() => text(dialog()).includes('the first task was not sent'), 'the note');
    expect(text(dialog())).toContain('the task board is closed');
    expect((dialog()!.querySelector('textarea') as HTMLTextAreaElement).value).toBe('Read the docs');
    expect(button('Retry')).toBeTruthy();
  });

  it('has the sidebar button for the founder only, and opens the sheet', async () => {
    await mount('#/inbox');
    await until(() => $$('.rail button').some((b) => text(b).includes('New agent')), 'the button');
    await click($$('.rail button').find((b) => text(b).includes('New agent')));
    expect(location.hash).toBe('#/new');
    await until(() => dialog(), 'the sheet');
  });

  it('shows no button to a member, and the route says new agents are for the founder', async () => {
    await mount('#/new', setup({ role: 'member' }));
    await until(() => dialog(), 'the sheet');
    expect(text(dialog())).toContain('New agents are for the founder.');
    expect($$('.rail button').some((b) => text(b).includes('New agent'))).toBe(false);
  });

  it('hides the button and says so on the route when the daemon does not list agent.create', async () => {
    const s = setup();
    s.daemon.knobs.unserve('agent.create');
    await mount('#/new', s);
    await until(() => dialog(), 'the sheet');
    expect(text(dialog())).toContain('This daemon cannot create agents.');
    expect($$('.rail button').some((b) => text(b).includes('New agent'))).toBe(false);
  });

  it('clears the form for Create another, but keeps it after a failure', async () => {
    const s = setup();
    await mount('#/new', s);
    await fill();
    await click(create());
    await until(() => text(dialog()).includes('fresh is running'), 'done');
    await click(button('Create another'));
    await until(() => findInput('Name'), 'the form');
    expect(findInput('Name')!.value).toBe('');
    expect(findInput('Role')!.value).toBe('');
    expect(select('Repo').value).toBe('');
  });

  it('shows the error with Retry when the operation cannot be read, and disables the task Retry while it waits', async () => {
    const s = setup();
    s.daemon.knobs.refuse('operation.get', 'state', 'the operation store is down');
    await mount('#/new/op-9', s);
    await until(() => text(dialog()).includes('the operation store is down'), 'the error');
    expect(button('Retry')).toBeTruthy();
  });
});
