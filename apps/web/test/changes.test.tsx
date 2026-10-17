// The Changes tab: the + menu opens it, the changed files list on the left, a click shows one file's diff; against the fake daemon.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV => {
  const data = new Map<string, string>();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const click = (el: Element) => act(async () => void (el as HTMLElement).click());

async function mount(s = setup(), agent = ARCH) {
  const store = new Store(s.client);
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
  await until(() => box.querySelector('[aria-label="Add a tab"]'), 'the tab strip');
  return s;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

const item = (name: string) => [...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent === name);
const files = () => [...box.querySelectorAll<HTMLElement>('.changes-tab .changed button')].map((b) => b.textContent ?? '');
const diff = () => box.querySelector('.changes-tab .diff')?.textContent ?? '';

describe('Changes tab', () => {
  it("opens from the + menu, lists the changed files, and shows one file's diff when picked", async () => {
    const s = setup();
    await mount(s);
    await click(box.querySelector('[aria-label="Add a tab"]')!);
    await click(item('Changes')!);
    await until(() => files().length === 2, 'the file list');
    expect(files()[0]).toContain('All 1 files');
    expect(files()[1]).toContain('docs/pc-app-gaps.md');
    await until(() => diff().includes('docs/pc-app-gaps.md'), 'the whole diff');
    await click([...box.querySelectorAll<HTMLElement>('.changes-tab .changed button')][1]!);
    await until(() => diff().includes('+added line 1'), 'the file diff');
    expect(diff()).toContain('-removed line 1');
    expect(box.querySelector('.changes-tab .diff .add')).not.toBeNull();
    expect([...box.querySelectorAll('.changes-tab button')].map((b) => b.textContent)).toContain('Open outside');
  });

  it('says so when nothing changed', async () => {
    await mount(setup(), 'handline.auditor');
    await click(box.querySelector('[aria-label="Add a tab"]')!);
    await click(item('Changes')!);
    await until(() => box.querySelector('.changes-tab')?.textContent?.includes('Nothing changed'), 'the empty line');
  });

  it('finds the worktree when another agent owns it', async () => {
    const s = setup();
    s.daemon.admin.worktrees.rows.find((w) => w.owner === ARCH)!.owner = 'handline.auditor';
    await mount(s);
    await click(box.querySelector('[aria-label="Add a tab"]')!);
    await click(item('Changes')!);
    await until(() => files().length === 2, 'the file list');
  });

  it('names the status in words', async () => {
    await mount();
    await click(box.querySelector('[aria-label="Add a tab"]')!);
    await click(item('Changes')!);
    await until(() => files().length === 2, 'the file list');
    expect(files()[1]).toContain('Modified');
  });

  it('Reload fetches the open diff again', async () => {
    const s = setup();
    await mount(s);
    await click(box.querySelector('[aria-label="Add a tab"]')!);
    await click(item('Changes')!);
    await until(() => diff().includes('docs/pc-app-gaps.md'), 'the diff');
    const calls: string[] = [];
    const call = s.client.call.bind(s.client) as (m: string, p?: unknown) => Promise<unknown>;
    (s.client as unknown as { call: typeof call }).call = (m, p) => (calls.push(m), call(m, p));
    await click([...box.querySelectorAll<HTMLElement>('.changes-tab button')].find((b) => b.textContent === 'Reload')!);
    await until(() => calls.includes('worktree.diff') && calls.includes('worktrees.list'), 'both refetched');
  });

  it('says to pick the file when only untracked files are listed', async () => {
    const s = setup();
    const row = s.daemon.admin.worktrees.rows.find((w) => w.owner === ARCH)!;
    row.changed = [{ path: 'new.md', status: '?', added: 0, deleted: 0 }];
    await mount(s);
    await click(box.querySelector('[aria-label="Add a tab"]')!);
    await click(item('Changes')!);
    await until(() => box.querySelector('.changes-tab')?.textContent?.includes('Untracked files: pick the file'), 'the note');
  });
});
