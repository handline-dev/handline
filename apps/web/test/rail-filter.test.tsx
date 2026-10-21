// The rail's filter button says when it hides agents, and a group with any dot hidden is not shown as checked.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (init: Record<string, string>): KV => {
  const data = new Map(Object.entries(init));
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
async function mount(hidden: string) {
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(setup().client)} daemon="test" storage={memory({ hiddenDots: hidden })} />);
  });
  await until(() => box.querySelector('[data-agent]'), 'the agent rows');
}
const filter = () => box.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"][aria-label^="View"]')!;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('the rail filter button', () => {
  it('shows nothing special while nothing is hidden', async () => {
    await mount('');
    expect(filter().getAttribute('aria-label')).toBe('View');
    expect(box.querySelector('[data-testid="filter-active"]')).toBeNull();
  });

  it('is marked and counts the hidden dots, and Sleeping is not checked while one of its dots is hidden', async () => {
    await mount('parked');
    await until(() => filter().getAttribute('aria-label') === 'View: 1 hidden', 'the stored setting to move into prefs');
    expect(filter().getAttribute('aria-label')).toBe('View: 1 hidden');
    expect(box.querySelector('[data-testid="filter-active"]')).not.toBeNull();
    await act(async () => filter().click());
    await until(() => document.querySelector('[role="menuitemcheckbox"]'), 'the filter menu');
    const sleeping = [...document.querySelectorAll('[role="menuitemcheckbox"]')].find((i) => i.textContent?.includes('Sleeping'));
    expect(sleeping?.getAttribute('aria-checked')).toBe('false');
  });
});
