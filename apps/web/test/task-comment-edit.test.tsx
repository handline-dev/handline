// Edit and delete a comment: the author and the founder get both, a member gets none on another agent's; an edit shows at once and rolls back with the reason; Delete offers Undo before the daemon is asked.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { UNDO_MS } from '#src/TaskActivity.tsx';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

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
  const data = new Map<string, string>();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const section = () => box.querySelector<HTMLElement>('.tbody');
const comments = () => [...section()!.querySelectorAll<HTMLElement>('.comment')];
const button = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === label || b.getAttribute('aria-label') === label) as
    HTMLButtonElement | undefined;
/** The item of a comment's three-dot menu: it opens the menu first. */
const item = async (c: HTMLElement, label: string) => {
  await act(async () => void document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
  await act(async () => void button(c, 'More')?.click());
  return [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => i.textContent === label);
};
/** Replaces the text of the in-place editor, as typing would. */
const typeIn = async (form: HTMLElement, value: string) => {
  await until(() => form.querySelector('.ProseMirror'), 'the editor');
  await act(async () => void (form.querySelector('.ProseMirror')!.textContent = value));
  await wait(50);
};
const sheet = (label: string) => section()!.querySelector<HTMLElement>(`[aria-label="${label}"]`);

async function mount(role: 'founder' | 'member' = 'founder') {
  const s = setup({ role });
  s.daemon.tasks.verbs = true;
  s.daemon.tasks.memo({ task: 1688, text: 'mine' }, 'add', 'web-test');
  s.daemon.tasks.memo({ task: 1688, text: 'theirs' }, 'add', 'handline.architect');
  location.hash = '#/tasks/1688';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => comments().length === 2, 'the comments');
  return s;
}
const text = (s: ReturnType<typeof setup>) => s.daemon.tasks.get({ task: 1688 }).memos.map((m: { text: string }) => m.text);

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('Edit and Delete on a comment', () => {
  it('gives the founder both on every comment', async () => {
    await mount();
    for (const c of comments()) {
      expect(await item(c, 'Edit')).toBeDefined();
      expect(await item(c, 'Delete')).toBeDefined();
    }
  });

  it('gives a member both on their own comment only', async () => {
    await mount('member');
    const [mine, theirs] = comments();
    expect(mine!.textContent).toContain('mine');
    expect(await item(mine!, 'Edit')).toBeDefined();
    expect(button(theirs!, 'More')).toBeUndefined();
  });

  it('shows an edit at once and the daemon keeps it', async () => {
    const s = await mount();
    await act(async () => void (await item(comments()[0]!, 'Edit'))!.click());
    const form = sheet('Edit comment')!;
    await typeIn(form, 'mine, changed');
    await act(async () => void button(form, 'Save')!.click());
    await until(() => comments()[0]!.textContent!.includes('mine, changed'), 'the edit in the list');
    await until(() => text(s).includes('mine, changed'), 'the daemon has it');
    expect(sheet('Edit comment')).toBeNull();
  });

  it('rolls an edit back and says why when the daemon refuses', async () => {
    const s = await mount();
    s.daemon.knobs.refuse('task.comment.edit', 'state', 'the task is closed', undefined, 1);
    await act(async () => void (await item(comments()[0]!, 'Edit'))!.click());
    const form = sheet('Edit comment')!;
    await typeIn(form, 'never kept');
    await act(async () => void button(form, 'Save')!.click());
    await until(() => sheet('Edit comment')?.textContent?.includes('the task is closed'), 'the refusal');
    expect(form.textContent).toContain('never kept'); // the box keeps what was typed, so it can be fixed or cancelled
    await act(async () => void button(form, 'Cancel')!.click());
    expect(comments()[0]!.textContent).toContain('mine');
    expect(comments()[0]!.textContent).not.toContain('never kept');
    expect(text(s)).toContain('mine');
  });

  it('takes a deleted comment out at once and Undo keeps it', async () => {
    const s = await mount();
    await act(async () => void (await item(comments()[0]!, 'Delete'))!.click());
    expect(section()!.textContent).toContain('Comment deleted');
    expect(text(s)).toContain('mine');
    await act(async () => void button(section()!, 'Undo')!.click());
    expect(section()!.textContent).not.toContain('Comment deleted');
    await wait(50);
    expect(text(s)).toContain('mine');
  });

  it('asks the daemon once the Undo time is over', async () => {
    const s = await mount();
    const del = await item(comments()[0]!, 'Delete');
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    await act(async () => void del!.click());
    await act(async () => void vi.advanceTimersByTime(UNDO_MS));
    vi.useRealTimers();
    await until(() => !text(s).includes('mine'), 'the daemon dropped it');
  });

  it('puts a refused Delete back and says why', async () => {
    const s = await mount();
    s.daemon.knobs.refuse('task.comment.delete', 'state', 'the task is closed', undefined, 1);
    const del = await item(comments()[0]!, 'Delete');
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    await act(async () => void del!.click());
    await act(async () => void vi.advanceTimersByTime(UNDO_MS));
    vi.useRealTimers();
    await until(() => section()!.textContent?.includes('the task is closed'), 'the refusal');
    expect(comments().length).toBe(2);
    expect(text(s)).toContain('mine');
  });

  it('edits in place: one box where the text was, no Source link, no second copy, Save and Cancel under it', async () => {
    await mount();
    const c = comments()[0]!;
    await act(async () => void (await item(c, 'Edit'))!.click());
    const form = sheet('Edit comment')!;
    expect(c.contains(form)).toBe(true);
    expect(form.textContent).not.toContain('Edit comment');
    expect(button(form, 'Source')).toBeUndefined();
    expect(c.querySelectorAll('.ProseMirror').length).toBe(1);
    expect(c.querySelector('.md')).toBeNull();
    const buttons = [...form.querySelectorAll('button')].map((b) => b.textContent);
    expect(buttons).toEqual(['Cancel', 'Save']);
  });

  it('Escape cancels and keeps the text', async () => {
    const s = await mount();
    await act(async () => void (await item(comments()[0]!, 'Edit'))!.click());
    const form = sheet('Edit comment')!;
    await typeIn(form, 'dropped');
    await act(
      async () => void form.querySelector('.ProseMirror')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })),
    );
    expect(sheet('Edit comment')).toBeNull();
    expect(text(s)).toContain('mine');
  });
});
