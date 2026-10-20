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
  it('the status bar says memory left, CPU and disk left in one line, red only for a cell that is over', async () => {
    await mount('#/my-turn');
    await until(() => text(box.querySelector('.statusbar')).includes('memory 38% left'), 'the resources cell');
    const bar = box.querySelector('.statusbar')!;
    expect(text(bar)).toContain('memory 38% left · CPU ok · disk 120GB left');
    expect(bar.querySelector('[data-over]')).toBeNull();
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
