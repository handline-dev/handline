// The sidebar's search box opens the palette, and Tasks carries a count when tasks wait on the person; the agent menu marks what is unseen as read.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';
import { Toast } from '#src/ds/Floating.tsx';

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

let root: Root | null = null;
let box: HTMLElement;
let keep = () => {};
async function mount(s: ReturnType<typeof setup>, hash = `#/agents/${ARCH}`) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
}
afterEach(async () => {
  keep();
  keep = () => {};
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('the sidebar search and the agent menu', () => {
  it('shows a search box with its shortcut that opens the palette', async () => {
    const s = setup();
    await mount(s);
    await until(() => box.querySelector('.rail-search'), 'the search box');
    expect(box.querySelector('.rail-search')!.textContent).toContain('⌘K');
    expect(document.querySelector('.palette')).toBeNull();
    await act(async () => box.querySelector<HTMLElement>('.rail-search')!.click());
    await until(() => document.querySelector('.palette'), 'the palette');
  });

  it('offers Mark as read in an agent menu only while something from it is unseen', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = { founder_unread: 2 } as never;
    await mount(s);
    const menu = async () => {
      await act(
        async () =>
          void box.querySelector(`[data-agent="${ARCH}"] .row-main`)!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true })),
      );
      await until(() => document.querySelector('[role=menu]'), 'the menu');
      return [...document.querySelectorAll('[role=menuitem]')].map((i) => i.textContent);
    };
    await until(() => box.querySelector(`[data-agent="${ARCH}"] .unread`), 'the unseen count');
    expect(await menu()).toContain('Mark as read');
  });
  const chat = (s: ReturnType<typeof setup>) => (s.daemon as unknown as { chat: { seenUpTo: Map<string, number> } }).chat;
  const openMenu = async () => {
    await act(
      async () =>
        void box.querySelector(`[data-agent="${ARCH}"] .row-main`)!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true })),
    );
    await until(() => document.querySelector('[role=menu]'), 'the menu');
    return [...document.querySelectorAll<HTMLElement>('[role=menuitem]')];
  };

  it('Mark as read sends chat.seen up to the newest message from the agent, past 60 newer ones that are not', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = { founder_unread: 1 } as never;
    const c = (s.daemon as unknown as { chat: { seed: (a: string, m: object) => number } }).chat;
    const newest = c.seed(ARCH, { body: 'Newest from the agent', from: ARCH, to: 'web-test' });
    for (let i = 0; i < 60; i++) c.seed(ARCH, { body: `mine ${i}`, from: 'web-test', to: ARCH });
    await mount(s, '#/requests');
    await until(() => box.querySelector(`[data-agent="${ARCH}"] .unread`), 'the unseen count');
    const item = (await openMenu()).find((i) => i.textContent === 'Mark as read')!;
    await act(async () => void item.click());
    await until(() => chat(s).seenUpTo.get(ARCH) === newest, 'chat.seen');
  });

  it('has no Mark as read when nothing is unseen', async () => {
    const s = setup();
    await mount(s, '#/requests');
    await until(() => box.querySelector(`[data-agent="${ARCH}"]`), 'the row');
    expect((await openMenu()).map((i) => i.textContent)).not.toContain('Mark as read');
  });

  const tasksButton = () => [...box.querySelectorAll('.rail-nav button')].find((b) => b.textContent?.includes('Tasks'))!;

  it('counts the task cards waiting on the person in Tasks', async () => {
    const s = setup();
    for (const id of [9201, 9202]) s.daemon.tasks.seed({ id, from: ARCH, to: 'web-test', title: `Task ${id}`, state: 'doing' });
    await mount(s, '#/requests');
    await until(() => tasksButton()?.textContent!.trim() === 'Tasks2', 'the count');
  });

  it('shows no count on Tasks when no task card waits', async () => {
    const s = setup();
    await mount(s, '#/requests');
    await until(() => tasksButton(), 'the Tasks button');
    await wait(100);
    expect(tasksButton().textContent!.trim()).toBe('Tasks');
  });

  it('a long toast is one line with its full text in the title, and its Undo stays', async () => {
    const long = 'Merge PR #798 and then every other pull request waiting in the queue, in order, and tell every agent about it · Merged';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<Toast action="Undo">{long}</Toast>));
    expect(box.querySelector('[role=status] > span')!.className).toContain('truncate');
    expect(box.querySelector('[role=status]')!.getAttribute('title')).toBe(long);
    const undo = box.querySelector('button')!;
    expect(undo.textContent).toBe('Undo');
    expect(undo.className).toContain('flex-none');
  });

  it('keeps Send in the composer when the model menu is wide', async () => {
    const s = setup();
    await mount(s);
    await until(() => box.querySelector('.compose-send'), 'Send');
    expect(box.querySelector('.compose-send')!.className).toContain('shrink-0');
    await until(() => box.querySelector('[title^="Model"]'), 'the model menu');
    expect(box.querySelector('[title^="Model"]')!.parentElement!.parentElement!.className).toContain('min-w-0');
  });
});
