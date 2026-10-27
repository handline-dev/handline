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
  it('the status bar has one text cell of what the agents use and a keep-awake choice; settings show only after a click', async () => {
    await mount('#/my-turn');
    await until(() => box.querySelector('.resource-cell'), 'the resource cell');
    expect(text(box.querySelector('.resource-cell'))).toMatch(/^Agents: CPU \d+%, .* · Memory .* free \(\d+%\)$/);
    expect(text(box.querySelector('.statusbar'))).toContain('Awake: Off');
    expect(box.querySelector('.limit-editor')).toBeNull();
    await act(async () => void box.querySelector<HTMLElement>('.resource-cell')!.click());
    await until(() => box.querySelector('.resource-details'), 'the popover');
    expect(box.querySelector('.limit-editor')).toBeNull(); // read-only until a row is clicked
    const load = [...box.querySelectorAll<HTMLElement>('.resource-details button')].find((b) => text(b).startsWith('Load'))!;
    expect(load.querySelector('svg')).not.toBeNull(); // the rows that open say so
    expect(box.querySelectorAll('.resource-details button svg').length).toBe(2);
    await act(async () => void load.click());
    await until(() => box.querySelector('.limit-editor'), 'the load editor');
    expect(text(box.querySelector('.limit-editor'))).toContain('Now 0.3 → starts go.');
    await act(async () => void box.querySelector<HTMLElement>('button[aria-label="Max"]')!.click());
    await until(() => text(box.querySelector('.resource-details')).includes('/ 3.0'), 'the new limit');
  });

  it('the admin view lists agents heaviest first with a Stop button each and a totals line', async () => {
    await mount('#/admin');
    await until(() => box.querySelector('.admin-usage li[data-agent]'), 'the usage rows');
    const rows = [...box.querySelectorAll('.admin-usage li[data-agent]')];
    const cpu = rows.map((r) => Number(/CPU (\d+)%/.exec(text(r))![1]));
    expect(cpu).toEqual([...cpu].sort((a, b) => b - a));
    expect(rows.every((r) => r.querySelector('button')?.textContent === 'Stop')).toBe(true);
    expect(text(box.querySelector('.admin-usage-total'))).toMatch(/^\d+ agents · CPU \d+% · memory/);
  });
});
