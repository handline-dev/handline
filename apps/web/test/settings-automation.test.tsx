// The Home settings page's Automation rows: the handoff prompt as one line, and the switch that ignores cache expiry.
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
const setValue = (el: HTMLInputElement | HTMLTextAreaElement, value: string) =>
  act(async () => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const blur = (el: HTMLElement) => act(async () => void el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
const values = (s: ReturnType<typeof setup>) => s.daemon.admin.settings().values as Record<string, unknown>;

async function mount(s = setup(), folds = true) {
  location.hash = '#/settings';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  if (folds) {
    await openFolds();
    await until(() => $('[data-setting="Handoff text"] button'), 'the Handoff text row');
    await act(async () => $('[data-setting="Handoff text"] button')!.click()); // the prompt is folded behind this row
  }
  return s;
}
const scrollIntoView = Element.prototype.scrollIntoView;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  Element.prototype.scrollIntoView = scrollIntoView;
});

const prompt = () => $('textarea[aria-label="What the agent is asked"]') as HTMLTextAreaElement;
const card = () => $('[data-setting="What the agent is asked"]')!;
const reads = async () => {
  if (!$('[data-reads]')) await click($('[data-setting] button[aria-expanded]')!);
  return $('[data-reads]')!.textContent;
};
const button = (label: string) =>
  [...card().querySelectorAll('button')].find(
    (b) => b.getAttribute('aria-label') === label || b.textContent === label,
  ) as HTMLButtonElement;
const click = (el: HTMLElement) => act(async () => void el.click());

describe('settings: handoff prompt and cache expiry', () => {
  it('shows the built-in text tagged Built-in, with Reset grey and what the agent reads', async () => {
    const s = await mount();
    await until(() => prompt(), 'the box');
    expect(prompt().value).toBe(values(s).handoff_prompt);
    expect(card().textContent).toContain('Built-in');
    expect(button('Reset to built-in').disabled).toBe(true);
    expect($('[data-reads]')).toBeNull();
    const text = await reads();
    expect(text).toContain("(the note's file)");
    expect(text).toContain('3000');
    expect(text).not.toContain('{');
  });

  it('an edit sends handoff_prompt only, line breaks joined; Reset sends blank and goes grey again', async () => {
    const s = await mount();
    await until(() => prompt(), 'the box');
    const before = { ...values(s) };
    await setValue(prompt(), 'Write it to {path}.\nShort and plain.');
    await blur(prompt());
    await until(() => values(s).handoff_prompt === 'Write it to {path}. Short and plain.', 'the save');
    expect({ ...values(s), handoff_prompt: before.handoff_prompt }).toEqual(before);
    await until(() => !button('Reset to built-in').disabled, 'Reset on');
    expect(card().textContent).not.toContain('Built-in');
    await click(button('Reset to built-in'));
    await until(() => values(s).handoff_prompt === before.handoff_prompt, 'back to the built-in');
    await until(() => button('Reset to built-in').disabled, 'Reset grey');
  });

  it('refuses under 20 characters with the way out, and blank is the built-in', async () => {
    const s = await mount();
    await until(() => prompt(), 'the box');
    const before = values(s).handoff_prompt;
    await setValue(prompt(), 'too short');
    await blur(prompt());
    const refusal = card().querySelector('[role="alert"]')!;
    expect(refusal.textContent).toContain('Not saved, the old text stays. Too short to guide a note. Leave it blank for the built-in.');
    expect(refusal.previousElementSibling).toBe(prompt());
    expect(prompt().getAttribute('aria-invalid')).toBe('true');
    expect(card().textContent).not.toContain('Built-in');
    expect(button('Reset to built-in').disabled).toBe(false);
    expect(values(s).handoff_prompt).toBe(before);
    await click(button('Reset to built-in'));
    expect(prompt().value).toBe(before);
    expect(card().querySelector('[role="alert"]')).toBeNull();
    expect(values(s).handoff_prompt).toBe(before);
    await setValue(prompt(), 'too short');
    await blur(prompt());
    await setValue(prompt(), 'A different text that is long enough.');
    await blur(prompt());
    await until(() => values(s).handoff_prompt === 'A different text that is long enough.', 'the save');
    await setValue(prompt(), '  ');
    await blur(prompt());
    await until(() => values(s).handoff_prompt === before, 'blank is the built-in');
  });

  it('shows the path on its own line in the preview when the text has no {path}', async () => {
    await mount();
    await until(() => prompt(), 'the box');
    await setValue(prompt(), 'Write your handoff note now.');
    expect(await reads()).toBe("Write your handoff note now.\n(the note's file)");
  });

  it('warns when agents set their own text, and Show names them', async () => {
    const s = setup();
    s.daemon.admin.handoffOwn.push('handline.auditor', 'handline.term-build');
    await mount(s);
    await until(() => prompt(), 'the box');
    expect(card().textContent).toContain('2 agents set their own text, so they ignore this one.');
    expect(card().textContent).not.toContain('handline.auditor');
    await click(button('Show'));
    expect(card().textContent).toContain('handline.auditor, handline.term-build');
  });

  it('the switch sends ignore_cache_expiry', async () => {
    const s = await mount();
    await until(() => $('[data-setting="Ignore cache expiry"] [role="switch"]'), 'the switch');
    expect(values(s).ignore_cache_expiry).toBe(false);
    await click($('[data-setting="Ignore cache expiry"] [role="switch"]')!);
    await until(() => values(s).ignore_cache_expiry === true, 'the save');
  });

  it('groups the page as the pair: six groups, each in the contents list with an icon, the handoff text behind one row', async () => {
    await mount(setup(), false);
    await until(() => $('[data-setting="Handoff text"]'), 'the rows');
    const nav = [...document.querySelectorAll('nav[aria-label="Contents"] button')];
    expect(nav.map((b) => b.textContent)).toEqual(['Notifications', 'Work', 'Appearance', 'Devices', 'Always answer', 'This computer']);
    expect(nav.every((b) => b.querySelector('svg'))).toBe(true);
    expect([...document.querySelectorAll('.settings-body h3.shead')].every((h) => h.querySelector('svg'))).toBe(true);
    expect(prompt()).toBeNull();
  });

  it('a daemon without settings.get shows no rows', async () => {
    await mount(setup({ daemon: { admin: false } }), false);
    await wait(50);
    expect(prompt()).toBeNull();
  });
});

/** The Advanced and About sections start folded: open them once the page has drawn them. */
async function openFolds() {
  await until(() => document.querySelector('.settings-shell button[aria-expanded="false"]'), 'the folds');
  const folds = [...document.querySelectorAll<HTMLElement>('.settings-shell button[aria-expanded="false"]')].filter(
    (b) => !b.closest('[data-setting]'),
  );
  await act(async () => folds.forEach((b) => b.click()));
}
