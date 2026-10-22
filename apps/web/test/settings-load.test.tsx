// The Home settings page's machine load limits: three rows under the default cap, each saving its own key.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
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
const setValue = (el: HTMLInputElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const blur = (el: HTMLElement) => act(async () => void el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
const field = (label: string) => $(`[data-setting="${label}"] input`) as HTMLInputElement;
const values = (s: ReturnType<typeof setup>) => s.daemon.admin.settings().values as Record<string, unknown>;

async function mount(s = setup()) {
  location.hash = '#/settings';
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
});

describe('settings: machine load limits', () => {
  it('shows the fake values, with 0 as off, and a change sends that key only', async () => {
    const s = await mount();
    await until(() => field('Load per core'), 'the rows');
    expect([field('Agents at once').value, field('Load per core').value, field('Least free memory').value]).toEqual(['off', '150', '10']);
    const before = { ...values(s) };
    await setValue(field('Load per core'), '200');
    await blur(field('Load per core'));
    await until(() => values(s).load_per_core_pct === 200, 'the save');
    expect({ ...values(s), load_per_core_pct: 150 }).toEqual(before);
    await setValue(field('Load per core'), 'off');
    await blur(field('Load per core'));
    await until(() => values(s).load_per_core_pct === 0, 'off saved as 0');
  });

  it('a bad number is not sent', async () => {
    const s = await mount();
    await until(() => field('Least free memory'), 'the rows');
    await setValue(field('Least free memory'), 'lots');
    await blur(field('Least free memory'));
    expect($('[data-setting="Least free memory"] .ui-note')?.textContent).toContain('whole number');
    expect(values(s).ram_free_min_pct).toBe(10);
  });

  it('a daemon without settings.get shows no rows', async () => {
    await mount(setup({ daemon: { admin: false } }));
    await wait(50);
    expect($('[data-setting="Load per core"]')).toBeNull();
  });
});
