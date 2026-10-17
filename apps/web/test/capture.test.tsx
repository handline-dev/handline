// Capture a window in the desktop shell: pick, note, and send as a new task or a comment; the picture rides as a reference in the text.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const dialogAtCapture: boolean[] = [];
let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const button = (label: string) => $$('button').find((b) => (b.textContent ?? '').trim().startsWith(label));
const typeInto = (el: HTMLTextAreaElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const choose = (el: HTMLSelectElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });

async function mount(hash: string) {
  const s = setup();
  s.daemon.tasks.verbs = true;
  Object.assign(window, {
    handline: {
      api: 1,
      screen: async () => ({ ok: true }),
      unsent: async () => ({ ok: true }),
      captureWindows: async () => ({
        ok: true,
        windows: [
          { id: 'window:7', name: 'Simulator', thumb: `data:image/png;base64,${PNG}` },
          { id: 'window:throws', name: 'Broken', thumb: `data:image/png;base64,${PNG}` },
        ],
      }),
      captureWindow: async (a: { id: string }) => {
        dialogAtCapture.push(!!document.querySelector('.ui-dialog'));
        if (a.id === 'window:throws') throw new Error('ipc down');
        return a.id === 'window:7' ? { ok: true, name: 'Simulator.png', png: PNG } : { ok: false, code: 'not_found', message: 'gone' };
      },
    },
  });
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  delete (window as { handline?: unknown }).handline;
  location.hash = '';
});

/** The shortcut: the Tasks screens have no Capture button, the palette and ⌘⇧C open the dialog. */
const capture = () => act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', metaKey: true, shiftKey: true, bubbles: true })));

describe('capture', () => {
  it('sends the picture as a new task whose body names it', async () => {
    const s = await mount('#/tasks');
    await capture();
    await until(() => $$('[aria-label="Windows"] button').length, 'the windows');
    await click($$('[aria-label="Windows"] button')[0]);
    await until(() => $('[data-testid="capture-preview"]'), 'the preview');
    await typeInto($('#capture-note') as HTMLTextAreaElement, 'The header overlaps the title');
    await click(button('New task'));
    await choose($('select[aria-label="Owner"]') as HTMLSelectElement, 'handline.auditor');
    await click(button('Send'));
    await until(() => !$('[data-testid="capture-preview"]'), 'the dialog to close', 600);
    const list = (await s.client.call('tasks.list', {} as never)) as { items: { id: number; title: string }[] };
    const made = list.items.find((t) => t.title === 'The header overlaps the title')!;
    expect(made).toBeTruthy();
    const t = (await s.client.call('task.get', { task: made.id } as never)) as {
      body: string;
      body_parts?: { image?: { name: string } }[];
    };
    expect(t.body).toMatch(/^The header overlaps the title\n!\[Simulator\]\(attachment:[\w-]+\)$/);
    expect(t.body_parts?.[1]?.image?.name).toBe('Simulator.png');
  });

  it('takes the picture with the dialog out of the way, then shows it again', async () => {
    dialogAtCapture.length = 0;
    await mount('#/tasks');
    await capture();
    await until(() => $$('[aria-label="Windows"] button').length, 'the windows');
    await click($$('[aria-label="Windows"] button')[0]);
    await until(() => $('[data-testid="capture-preview"]'), 'the preview');
    expect(dialogAtCapture).toEqual([false]);
  });

  it('brings the dialog back when the capture call throws', async () => {
    await mount('#/tasks');
    await capture();
    await until(() => $$('[aria-label="Windows"] button').length, 'the windows');
    await click($$('[aria-label="Windows"] button')[1]);
    await until(() => $('.ui-dialog'), 'the dialog back');
    expect($$('[aria-label="Windows"] button').length).toBe(2);
  });

  it('adds the picture as a comment on the open task', async () => {
    const s = await mount('#/tasks/1684');
    await capture();
    await until(() => $$('[aria-label="Windows"] button').length, 'the windows');
    await click($$('[aria-label="Windows"] button')[0]);
    await until(() => $('[data-testid="capture-preview"]'), 'the preview');
    expect(button('Comment on T1684')).toBeTruthy();
    await click(button('Send'));
    await until(() => !$('[data-testid="capture-preview"]'), 'the dialog to close', 600);
    const t = (await s.client.call('task.get', { task: 1684 } as never)) as { memos: { text: string; parts?: unknown[] }[] };
    expect(t.memos.at(-1)!.text).toMatch(/!\[Simulator\]\(attachment:/);
    expect(t.memos.at(-1)!.parts).toBeTruthy();
  });

  it('adds the picture as a comment on a task picked from the list, from a page that is not a task', async () => {
    const s = await mount('#/tasks');
    await capture();
    await until(() => $$('[aria-label="Windows"] button').length, 'the windows');
    await click($$('[aria-label="Windows"] button')[0]);
    await until(() => $('[data-testid="capture-preview"]'), 'the preview');
    await click(button('New task'));
    await choose($('select[aria-label="Send as"]') as HTMLSelectElement, 'comment');
    await click(button('Send'));
    expect($('[role="alert"]')?.textContent).toBe('Pick the task.');
    await choose($('select[aria-label="Task"]') as HTMLSelectElement, '1684');
    await click(button('Send'));
    await until(() => !$('[data-testid="capture-preview"]'), 'the dialog to close', 600);
    const t = (await s.client.call('task.get', { task: 1684 } as never)) as { memos: { text: string }[] };
    expect(t.memos.at(-1)!.text).toMatch(/!\[Simulator\]\(attachment:/);
  });
});
