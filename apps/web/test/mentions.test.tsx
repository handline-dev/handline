import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
const data = new Map<string, string>();
const storage: KV = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
const text = (el: Element | null) => el?.textContent ?? '';
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await act(async () => void (await new Promise((r) => setTimeout(r, 10))));
  }
  throw new Error(`timed out waiting for ${what}`);
}
async function mount(hash: string, s = setup()) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={storage} />);
  });
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const typeInto = (el: HTMLTextAreaElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

describe('writing a comment and mentioning an agent', () => {
  it('letters typed in the comment box are not task shortcuts, and ⌘↵ sends the comment', async () => {
    const s = setup();
    s.daemon.tasks.verbs = true;
    await mount('#/tasks/1688', s);
    await until(() => $('.body-editor'), 'the comment editor');
    const editable = $('.body-editor [contenteditable]') ?? $('.body-editor')!;
    await act(async () => void editable.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', bubbles: true })));
    expect($('[role="group"][aria-label$="why (optional):"]')).toBeNull(); // c in the box does not open Cancel
    const source = [...box.querySelectorAll('button')].find((b) => b.textContent === 'Source')!;
    await act(async () => void source.click());
    const area = $('textarea[aria-label="Comment"]') as HTMLTextAreaElement;
    await typeInto(area, 'sent with the keys');
    await act(
      async () => void area.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true, cancelable: true })),
    );
    await until(() => text($('section[aria-label="Activity"]')).includes('sent with the keys'), 'the comment');
  });

  it('@ in the chat box lists agents and a pick puts the name in', async () => {
    await mount('#/agents/handline.architect');
    await until(() => $('textarea.compose'), 'the message box');
    const box_ = $('textarea.compose') as HTMLTextAreaElement;
    await typeInto(box_, 'ask @aud');
    await until(() => $('[role="option"]'), 'the agent list');
    expect([...box.querySelectorAll('[role="option"]')].some((o) => text(o).includes('@handline.auditor'))).toBe(true);
    await act(async () => void box.querySelector<HTMLElement>('[role="option"] button')!.click());
    expect((box_.value || '').startsWith('ask @')).toBe(true);
  });
});
