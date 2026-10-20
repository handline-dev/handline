// The Home settings page's notifications, background work, appearance and connection sections on the fake daemon.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
  const data = new Map<string, string>([['rail', '0']]);
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const setValue = (el: HTMLInputElement | HTMLSelectElement, value: string) =>
  act(async () => {
    const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
const blur = (el: HTMLElement) => act(async () => void el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
const field = (label: string) => $(`[data-setting="${label}"] input`) as HTMLInputElement;

async function mount(hash: string, s = setup()) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  return s;
}
const scrollIntoView = Element.prototype.scrollIntoView;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  Element.prototype.scrollIntoView = scrollIntoView;
  vi.unstubAllGlobals();
});

describe('settings: more sections', () => {
  it('the notice batch and the background timings save as whole seconds', async () => {
    const s = await mount('#/settings');
    await until(() => field('Notice batch'), 'the fields');
    await setValue(field('Notice batch'), '90');
    await blur(field('Notice batch'));
    await until(() => (s.daemon.admin.settings().values as Record<string, unknown>).notice_digest_s === 90, 'the batch');
    await setValue(field('Background grace'), '30');
    await blur(field('Background grace'));
    await until(() => (s.daemon.admin.settings().values as Record<string, unknown>).bg_grace_s === 30, 'the grace');
    await setValue(field('Background check'), 'soon');
    await blur(field('Background check'));
    expect(text($('[data-setting="Background check"] .ui-note'))).toContain('whole seconds');
  });

  it('the theme is this window only: it sets data-theme and is kept in the browser', async () => {
    await mount('#/settings');
    await until(() => $('[aria-label="Theme"]'), 'the theme');
    const light = $$('[aria-label="Theme"] button').find((b) => b.title === 'Light')!;
    await click(light);
    expect(document.documentElement.dataset.theme).toBe('light');
    await click($$('[aria-label="Theme"] button').find((b) => b.title === 'System'));
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it('the connection is read only', async () => {
    await mount('#/settings');
    await until(() => $('[data-setting="Role"]'), 'the connection');
    expect($('[data-setting="Role"] input')).toBeNull();
  });
});
