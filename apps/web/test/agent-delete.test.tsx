// Delete agent: the last item of the sidebar agent menu, in danger style; a confirm sheet says what goes and what stays, the daemon's refusal shows as it is.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const TB = 'handline.term-build'; // parked: no session
const AUD = 'handline.auditor'; // has an open session
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 300) {
  for (let i = 0; i < tries; i++) {
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
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const text = (el: Element | null) => el?.textContent ?? '';
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const row = (agent: string) => $(`[data-agent="${agent}"]`);
const menuItem = (label: string) => [...document.querySelectorAll<HTMLElement>('[role=menuitem]')].find((b) => text(b) === label);
const openMenu = (agent: string) =>
  act(async () => void row(agent)!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true })));

/** Opens the agent menu and clicks Delete agent once the item and the sheet have rendered. */
async function openSheet(agent: string) {
  await openMenu(agent);
  await until(() => menuItem('Delete agent'), 'the menu item');
  await click(menuItem('Delete agent')!);
  await until(() => $('[role=alertdialog]'), 'the sheet');
  return $('[role=alertdialog]')!;
}

async function mount() {
  const s = setup();
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => row(TB) && row(AUD), 'the rows');
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('Delete agent', () => {
  it('is the last item of the agent menu; the sheet says what goes and what stays, and Delete removes the row', async () => {
    const s = await mount();
    await openMenu(TB);
    await until(() => menuItem('Delete agent'), 'the menu item');
    const items = [...document.querySelectorAll('[role=menuitem]')];
    expect(text(items.at(-1)!)).toBe('Delete agent');
    await click(menuItem('Delete agent')!);
    await until(() => $('[role=alertdialog]'), 'the sheet');
    const sheet = $('[role=alertdialog]')!;
    expect(text(sheet)).toContain(`Delete ${TB}?`);
    expect(text(sheet)).toMatch(/team entry, schedule and keys go/);
    expect(text(sheet)).toMatch(/Past messages, tasks and events stay\. A worktree with nothing in it goes/);
    await click([...sheet.querySelectorAll('button')].find((b) => text(b) === 'Delete')!);
    await until(() => !row(TB), 'the row gone', 600);
    expect(s.daemon.sessions.has(TB)).toBe(false);
    expect($('[role=alertdialog]')).toBeNull();
  });

  it("keeps the sheet open and shows the daemon's reason as it is when the agent is refused", async () => {
    const s = await mount();
    const sheet = await openSheet(AUD);
    await click([...sheet.querySelectorAll('button')].find((b) => text(b) === 'Delete')!);
    await until(() => $('[role=alertdialog] [role=alert]'), 'the reason');
    expect(text($('[role=alertdialog] [role=alert]'))).toContain('has a session running');
    expect(row(AUD)).not.toBeNull();
    expect(s.daemon.sessions.has(AUD)).toBe(true);
  });

  it('Cancel closes the sheet and sends nothing', async () => {
    const s = await mount();
    const sheet = await openSheet(TB);
    await click([...sheet.querySelectorAll('button')].find((b) => text(b) === 'Cancel')!);
    expect($('[role=alertdialog]')).toBeNull();
    expect(s.daemon.sessions.has(TB)).toBe(true);
  });

  it('asks what to do with a worktree that holds work, and sends the delete again with the answer', async () => {
    const s = await mount();
    s.daemon.held.set(TB, [{ path: '/w/tb', files_changed: 3, commits_ahead: 2 }]);
    const sheet = await openSheet(TB);
    await click([...sheet.querySelectorAll('button')].find((b) => text(b) === 'Delete')!);
    await until(() => $('[role=alertdialog]')?.textContent?.includes('has work in its worktree'), 'the pop');
    const pop = $('[role=alertdialog]')!;
    expect(text(pop)).toContain('3 unsaved files, 2 unpushed commits');
    expect([...pop.querySelectorAll('button')].map(text)).toEqual([
      'Save to a branch, then delete',
      'Delete, keep the worktree',
      'Delete and lose the files',
      'Cancel',
    ]);
    expect(row(TB)).not.toBeNull();
    await click([...pop.querySelectorAll('button')].find((b) => text(b) === 'Delete, keep the worktree')!);
    await until(() => !row(TB), 'the row gone', 600);
    expect(s.daemon.sessions.has(TB)).toBe(false);
  });

  it('asks once more before it loses the files', async () => {
    const s = await mount();
    s.daemon.held.set(TB, [{ path: '/w/tb', files_changed: 1, commits_ahead: 0 }]);
    const sheet = await openSheet(TB);
    await click([...sheet.querySelectorAll('button')].find((b) => text(b) === 'Delete')!);
    await until(() => $('[role=alertdialog]')?.textContent?.includes('has work in its worktree'), 'the pop');
    await click([...$('[role=alertdialog]')!.querySelectorAll('button')].find((b) => text(b) === 'Delete and lose the files')!);
    expect(text($('[role=alertdialog] [role=alert]'))).toContain('Uncommitted files are lost');
    expect(s.daemon.sessions.has(TB)).toBe(true);
    await click([...$('[role=alertdialog]')!.querySelectorAll('button')].find((b) => text(b) === 'Delete')!);
    await until(() => !row(TB), 'the row gone', 600);
  });
});
