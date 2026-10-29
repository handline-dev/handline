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
async function mount(hash: string) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(setup().client)} daemon="test" storage={storage} />);
  });
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('system resources', () => {
  it('the status bar has cells for the CPU, memory and disk and a keep-awake choice; the popover is a read-only summary', async () => {
    await mount('#/my-turn');
    await until(() => box.querySelector('.resource-cell'), 'the resource cell');
    expect(text(box.querySelector('.resource-cell'))).toMatch(/^CPU \d+%.*Memory .* free.*Disk .* left/);
    expect(text(box.querySelector('.statusbar'))).toContain('Off');
    expect(box.querySelector('.limit-editor')).toBeNull();
    await act(async () => void box.querySelector<HTMLElement>('.resource-cell')!.click());
    await until(() => box.querySelector('.resource-details'), 'the popover');
    expect(['CPU', 'Memory', 'Disk'].map((l) => !!box.querySelector(`.resource-details [data-resource="${l}"]`))).toEqual([
      true,
      true,
      true,
    ]);
    expect(box.querySelector('.limit-editor')).toBeNull(); // a summary: the limits are set in Settings
    expect(box.querySelectorAll('.resource-details button').length).toBe(0);
  });

  it('the admin view lists agents heaviest first with Stop behind a menu on each row and a totals line', async () => {
    await mount('#/admin');
    await until(() => box.querySelector('.admin-usage li[data-agent]'), 'the usage rows');
    const rows = [...box.querySelectorAll('.admin-usage li[data-agent]')];
    const cpu = rows.map((r) => Number(/CPU (\d+)%/.exec(text(r))![1]));
    expect(cpu).toEqual([...cpu].sort((a, b) => b - a));
    expect(rows.some((r) => text(r).includes('parked'))).toBe(false);
    expect(rows.every((r) => r.querySelector('button[aria-label^="More for"]'))).toBe(true);
    expect(rows.some((r) => text(r).includes('Stop'))).toBe(false);
    expect(text(box.querySelector('.admin-usage-total'))).toMatch(/^\d+ agents · CPU \d+% · memory/);
  });
});
