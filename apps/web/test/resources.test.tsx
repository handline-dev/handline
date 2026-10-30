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
  it('the status bar has cells for the CPU, memory and disk and a keep-awake choice; the popover has the lines with their limits', async () => {
    await mount('#/my-turn');
    await until(() => box.querySelector('.resource-cell'), 'the resource cell');
    expect(text(box.querySelector('.resource-cell'))).toMatch(/^CPU \d+%.*Memory .* free.*Disk .* left/);
    expect(text(box.querySelector('.statusbar'))).toContain('Off');
    expect(box.querySelector('.limit-editor')).toBeNull();
    await act(async () => void box.querySelector<HTMLElement>('.resource-cell')!.click());
    await until(() => box.querySelector('.resource-details'), 'the popover');
    const line = (l: string) => box.querySelector<HTMLElement>(`.resource-details [data-resource="${l}"]`);
    expect(['Load', 'Memory', 'Disk', 'Heavy slots'].map((l) => !!line(l))).toEqual([true, true, true, true]);
    expect(text(line('Load'))).toContain('Limit 150%');
    expect(line('Disk')!.querySelector('button')).toBeNull(); // the daemon keeps no disk limit: nothing to change
    expect(box.querySelector('.limit-editor')).toBeNull(); // editors open on a click
  });

  it('a limit is changed in place: the line opens its editor, + saves at once, the effect is one line, and Undo puts it back', async () => {
    const s = setup();
    location.hash = '#/my-turn';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={storage} />);
    });
    await until(() => box.querySelector('.resource-cell'), 'the resource cell');
    await act(async () => void box.querySelector<HTMLElement>('.resource-cell')!.click());
    await until(() => box.querySelector('.resource-details'), 'the popover');
    const limit = () => s.daemon.admin.settings().values.load_per_core_pct;
    await act(async () => void box.querySelector<HTMLElement>('[data-resource="Load"] button')!.click());
    expect(text(box.querySelector('.limit-editor'))).toContain('Agents wait above 150%');
    expect(text(box.querySelector('.limit-editor'))).toContain('0 turns the limit off');
    await act(async () => void box.querySelector<HTMLElement>('[aria-label="Raise"]')!.click());
    await until(() => limit() === 200, 'the saved limit');
    await until(() => text(box.querySelector('.toasts')).includes('Load limit: 200%'), 'the line after the change');
    await act(async () => void [...box.querySelectorAll<HTMLElement>('.toasts button')].find((b) => text(b) === 'Undo')!.click());
    await until(() => limit() === 150, 'the limit back');
    const field = box.querySelector<HTMLInputElement>('[aria-label="Limit"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, '300');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => void (await new Promise((r) => setTimeout(r, 50))));
    expect(limit()).toBe(150); // typing saves nothing until the field is left
    await act(async () => void field.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
    await until(() => limit() === 300, 'the typed limit after blur');
  });

  it('a refused limit save leaves the limit as it was and says why', async () => {
    const s = setup();
    location.hash = '#/my-turn';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={storage} />);
    });
    await until(() => box.querySelector('.resource-cell'), 'the resource cell');
    await act(async () => void box.querySelector<HTMLElement>('.resource-cell')!.click());
    await until(() => box.querySelector('.resource-details'), 'the popover');
    s.daemon.knobs.refuse('settings.set', 'role', 'only the founder may change the limits');
    await act(async () => void box.querySelector<HTMLElement>('[data-resource="Load"] button')!.click());
    await act(async () => void box.querySelector<HTMLElement>('[aria-label="Raise"]')!.click());
    await until(() => document.body.textContent?.includes('only the founder may change the limits'), 'the refusal sentence');
    expect(s.daemon.admin.settings().values.load_per_core_pct).toBe(150);
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
