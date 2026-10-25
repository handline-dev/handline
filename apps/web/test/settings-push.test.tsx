// The Home settings page's Notifications switches: each sends its push_* key and shows the daemon's value.
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
const values = (s: ReturnType<typeof setup>) => s.daemon.admin.settings().values as Record<string, unknown>;
const toggle = (label: string) => $(`[data-setting="${label}"] [role="switch"]`);

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

const SWITCHES = [
  ['A question asked', 'push_prompt_asked'],
  ['A task done', 'push_task_done'],
  ['A stall', 'push_stall'],
  ['An account out', 'push_account_out'],
] as const;

describe('settings: push switches', () => {
  it.each(SWITCHES)('the %s switch sends %s and only that key', async (label, key) => {
    const s = await mount();
    await until(() => toggle(label), 'the switch');
    expect(toggle(label)!.getAttribute('aria-checked')).toBe('true');
    const before = { ...values(s) };
    await act(async () => void toggle(label)!.click());
    await until(() => values(s)[key] === false, 'the save');
    expect({ ...values(s), [key]: before[key] }).toEqual(before);
    await until(() => toggle(label)!.getAttribute('aria-checked') === 'false', 'the switch off');
  });
});
