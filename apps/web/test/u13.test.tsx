// U13: an agent's settings page. Each field saves on its own through agent.set (authored), a refusal puts the value back and says why in
// the field's own note, the two danger switches ask first, and the schedules add, edit and remove through the founder-only writes.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 300) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV => {
  const data = new Map<string, string>([['terminal', 'text']]);
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null) => el?.textContent ?? '';
const field = (key: string) => $(`[data-field="${key}"]`)!;
const control = <T extends HTMLElement>(key: string) => field(key).querySelector<T>('input, select, [role="switch"]')!;
const on = (key: string) => control(key).getAttribute('aria-checked') === 'true';
const btn = (label: string) => $$('button').find((b) => text(b).trim() === label)!;
const set = (el: HTMLElement, value: string, proto: object, type: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event(type, { bubbles: true }));
  });
const type = (key: string, value: string) => set(control(key), value, HTMLInputElement.prototype, 'input');
const blur = (el: HTMLElement) => act(async () => void el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
const session = (s: ReturnType<typeof setup>) => s.daemon.sessions.get(ARCH)!;

async function open(s = setup()) {
  location.hash = `#/agents/${ARCH}/settings`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => $('.settings-body'), 'the settings page');
  return s;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
  vi.unstubAllGlobals();
});

describe('U13 agent settings', () => {
  it('shows each field of the bridge page with the stored value', async () => {
    await open();
    expect(control<HTMLInputElement>('model').value).toBe('opus');
    expect(control<HTMLSelectElement>('effort').value).toBe('high');
    expect(control<HTMLInputElement>('context_k').value).toBe('300');
    expect(on('sandbox')).toBe(true);
    expect(on('skip_permissions')).toBe(false);
    for (const k of ['weight', 'handoff_note', 'repo', 'mode', 'browser', 'browser_sites', 'idle_close']) expect(field(k)).toBeTruthy();
  });

  it('a field of three options or fewer is a segmented control and picking one saves; more options is a select', async () => {
    const s = await open();
    expect(field('weight').querySelector('[role="radiogroup"]')).not.toBeNull();
    expect(field('weight').querySelector('select')).toBeNull();
    expect(field('handoff_note').querySelectorAll('[role="radio"]')).toHaveLength(3);
    expect(field('effort').querySelector('select')).not.toBeNull();
    expect(field('effort').querySelector('[role="radiogroup"]')).toBeNull();
    const other = $$('[data-field="weight"] [role="radio"]').find((r) => r.getAttribute('aria-checked') === 'false')!;
    const value = other.getAttribute('aria-label')!;
    await click(other);
    await until(() => session(s).settings.weight === value, 'saved');
    expect(
      $$('[data-field="weight"] [role="radio"]')
        .find((r) => r.getAttribute('aria-checked') === 'true')!
        .getAttribute('aria-label'),
    ).toBe(value);
  });

  it('a number shows its unit as a suffix inside the field, and what follows the comma goes in the tooltip', async () => {
    await open();
    expect(text(field('context_k').querySelector('.ui-input-wrap .ui-unit'))).toBe('k tokens');
    expect(text(field('idle_close').querySelector('.ui-input-wrap .ui-unit'))).toBe('min');
    expect(field('idle_close').querySelector('.srow-text')!.getAttribute('title')).toContain('0 = never');
  });

  it('a select saves on change, and the daemon holds the new value', async () => {
    const s = await open();
    await set(control('effort'), 'max', HTMLSelectElement.prototype, 'change');
    await until(() => session(s).settings.effort === 'max', 'saved');
    expect(control<HTMLSelectElement>('effort').value).toBe('max');
  });

  it('a number field saves on blur; a value that is not a number is not sent and its note says what is allowed', async () => {
    const s = await open();
    await type('context_k', 'abc');
    await blur(control('context_k'));
    expect(text(field('context_k'))).toContain('whole number');
    expect(session(s).settings.context_k).toBe('300');
    await type('context_k', '500');
    await blur(control('context_k'));
    await until(() => session(s).settings.context_k === '500', 'saved');
    expect(text(field('context_k'))).not.toContain('whole number');
  });

  it("a refused save puts the value back and the field's note gives the daemon's reason", async () => {
    const s = await open();
    s.daemon.knobs.refuse('agent.set', 'state', 'The daemon is busy; try again.');
    await set(control('effort'), 'max', HTMLSelectElement.prototype, 'change');
    await until(() => text(field('effort')).includes('busy'), 'the note');
    expect(control<HTMLSelectElement>('effort').value).toBe('high');
    expect(session(s).settings.effort).toBe('high');
  });

  it('skip prompts asks first in the bridge words; Cancel sends nothing and Turn on saves', async () => {
    const s = await open();
    await click(control('skip_permissions'));
    expect(text($('.verb-sheet.danger'))).toContain('it will act without asking, from the next launch');
    await click(btn('Cancel'));
    expect(on('skip_permissions')).toBe(false);
    expect(session(s).settings.skip_permissions).toBe('off');
    await click(control('skip_permissions'));
    await click(btn('Turn on'));
    await until(() => session(s).settings.skip_permissions === 'on', 'saved');
  });

  it('turning the sandbox off asks; turning it back on asks nothing', async () => {
    const s = await open();
    await click(control('sandbox'));
    expect(text($('.verb-sheet.danger'))).toContain('nothing holding them back');
    await click(btn('Turn off'));
    await until(() => session(s).settings.sandbox === 'off', 'off');
    await click(control('sandbox'));
    expect($('.verb-sheet.danger')).toBeNull();
    await until(() => session(s).settings.sandbox === 'on', 'on again');
  });

  it('sites are added and removed as a space-separated list', async () => {
    const s = await open();
    const input = $('[data-field="browser_sites"] input')!;
    await set(input, 'https://a.example', HTMLInputElement.prototype, 'input');
    await act(async () => void input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    await until(() => session(s).settings.browser_sites === 'https://a.example', 'added');
    await click($('[aria-label="Remove https://a.example"]')!);
    await until(() => session(s).settings.browser_sites === '', 'removed');
  });

  it('a member sees the page read-only, with no schedule controls', async () => {
    await open(setup({ role: 'member' }));
    expect(control<HTMLInputElement>('model').disabled).toBe(true);
    expect($$('button').some((b) => text(b) === 'Add schedule')).toBe(false);
  });
});

describe('U13 schedules', () => {
  it('adds a schedule shown in words with its cron, edits it, and removes it after a confirm', async () => {
    const s = await open();
    await click(btn('Add schedule'));
    await set($('[aria-label="When (cron)"]')!, '0 9 * * 1-5', HTMLInputElement.prototype, 'input');
    await set($('[aria-label="Message"]')!, 'Check the board', HTMLTextAreaElement.prototype, 'input');
    expect(text($('.verb-sheet'))).toContain('every weekday 09:00');
    await click(btn('Save'));
    await until(() => text($('.schedules')).includes('Check the board'), 'the line');
    expect(text($('.schedule'))).toContain('every weekday 09:00');
    await until(() => (s.daemon as unknown as { script: { schedules: unknown[] } }).script.schedules.length === 1, 'the daemon has it');
    await click($('[aria-label="Edit schedule"]')!);
    await set($('[aria-label="Message"]')!, 'Check the board twice', HTMLTextAreaElement.prototype, 'input');
    await click(btn('Save'));
    await until(() => text($('.schedules')).includes('twice'), 'edited');
    vi.stubGlobal('confirm', () => true);
    await click($('[aria-label="Remove schedule"]')!);
    await until(() => !$('.schedule'), 'removed');
    expect(text($('.schedules'))).toContain('none');
  });

  it('a refused add reverts and says why', async () => {
    const s = await open();
    s.daemon.knobs.refuse('schedule.add', 'role', 'Only the founder keeps schedules.');
    await click(btn('Add schedule'));
    await set($('[aria-label="When (cron)"]')!, '0 9 * * *', HTMLInputElement.prototype, 'input');
    await set($('[aria-label="Message"]')!, 'x', HTMLTextAreaElement.prototype, 'input');
    await click(btn('Save'));
    await until(() => text($('.schedules')).includes('Only the founder'), 'the reason');
    expect($('.schedule')).toBeNull();
  });
});
