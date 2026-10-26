// The Home settings page's Automation rows: the handoff prompt as one line, and the switch that ignores cache expiry.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { INVALID, Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';
import { parsePrompt } from '#src/Settings.tsx';

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
  await openFolds();
  return s;
}

/** The Advanced and About sections start folded: open them once the page has drawn them (a daemon without settings has none). */
async function openFolds() {
  for (let i = 0; i < 40; i++) {
    const folds = [...document.querySelectorAll<HTMLElement>('.settings-shell button[aria-expanded="false"]')];
    if (folds.length) return act(async () => folds.forEach((b) => b.click()));
    await act(async () => void (await new Promise((r) => setTimeout(r, 50))));
  }
}
const scrollIntoView = Element.prototype.scrollIntoView;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  Element.prototype.scrollIntoView = scrollIntoView;
});

const prompt = () => field('Handoff prompt');
const click = (el: HTMLElement) => act(async () => void el.click());

describe('settings: handoff prompt and cache expiry', () => {
  it('shows the daemon prompt, and an edit sends handoff_prompt only', async () => {
    const s = await mount();
    await until(() => prompt(), 'the row');
    expect(prompt().value).toBe(values(s).handoff_prompt);
    const before = { ...values(s) };
    await setValue(prompt(), ' Write it to {path}. ');
    await blur(prompt());
    await until(() => values(s).handoff_prompt === 'Write it to {path}.', 'the save');
    expect({ ...values(s), handoff_prompt: before.handoff_prompt }).toEqual(before);
  });

  it('does not accept a line break', () => {
    expect(parsePrompt('one\ntwo')).toBe(INVALID); // a text input drops a break itself; this guards what it did not
    expect(parsePrompt(' one ')).toBe('one');
  });

  it('says the path is added on its own line when the prompt has no {path}', async () => {
    const s = await mount();
    await until(() => prompt(), 'the row');
    expect($('[data-setting="Handoff prompt"]')?.textContent).not.toContain('added on a line of its own');
    await setValue(prompt(), 'Write your handoff.');
    await blur(prompt());
    await until(() => values(s).handoff_prompt === 'Write your handoff.', 'the save');
    await until(() => $('[data-setting="Handoff prompt"]')?.textContent?.includes('added on a line of its own'), 'the hint');
  });

  it('the switch sends ignore_cache_expiry', async () => {
    const s = await mount();
    await until(() => $('[data-setting="Ignore cache expiry"] [role="switch"]'), 'the switch');
    expect(values(s).ignore_cache_expiry).toBe(false);
    await click($('[data-setting="Ignore cache expiry"] [role="switch"]')!);
    await until(() => values(s).ignore_cache_expiry === true, 'the save');
  });

  it('a daemon without settings.get shows no rows', async () => {
    await mount(setup({ daemon: { admin: false } }));
    await wait(50);
    expect(prompt()).toBeNull();
  });
});
