// The New task form when the daemon refuses a title that repeats an open task: it names that task and offers to create it anyway.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV & { data: Map<string, string> } => {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string, from: ParentNode = box) => from.querySelector<HTMLElement>(sel);
const $$ = (sel: string, from: ParentNode = box) => [...from.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const typeInto = (el: HTMLTextAreaElement | HTMLInputElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const choose = (el: HTMLSelectElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
const press = (key: string, init: KeyboardEventInit = {}, el: Element = document.activeElement ?? box) =>
  act(async () => void el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })));
const page = () => $('section.tasks')!;
const rowIds = () => $$('tr.trow').map((r) => Number(r.dataset.task));
const button = (label: string, within: ParentNode = box) =>
  $$('button', within).find((b) => (text(b).replace(/\s+/g, ' ').trim() || b.getAttribute('aria-label') || '').startsWith(label));

async function mount(hash = '#/tasks', s = setup(), storage: KV = memory()) {
  const store = new Store(s.client);
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={storage} />);
  });
  return { store, storage, ...s };
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});
const loaded = () => until(() => rowIds().length, 'the task rows');
const focusPage = () => act(async () => page().focus());

describe('a duplicate title', () => {
  it('offers to create a task anyway when its title repeats an open task, naming that task', async () => {
    const s = setup();
    await mount('#/tasks', s);
    await loaded();
    await focusPage();
    await press('n');
    await choose($('.ui-dialog select') as HTMLSelectElement, 'handline.auditor');
    await typeInto($('.ui-dialog .tform input') as HTMLInputElement, 'Web UI design again');
    s.daemon.knobs.refuse('task.new', 'duplicate_title', 'T1684 (under T1) is open with nearly this title: "Web UI design"', 'add to that task instead', 1, { task: 1684 }); // local-ref-ok
    await click(button('Ask')!);
    await until(() => button('Create anyway'), 'the override');
    expect(text($('.ui-dialog [role=alert]'))).toContain('T1684 (under T1) is open'); // local-ref-ok
    expect(($('.ui-dialog a[href="#/tasks/1684"]') as HTMLAnchorElement | null)?.textContent).toBe('Open T1684'); // local-ref-ok
    expect(($('.ui-dialog .tform input') as HTMLInputElement).value).toBe('Web UI design again');
    await click(button('Create anyway')!);
    await until(() => $$('tr.trow').some((r) => text(r).includes('Web UI design again')), 'the new row');
    expect($('.ui-dialog')).toBeNull();
  });
});
