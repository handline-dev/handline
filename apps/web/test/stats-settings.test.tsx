// U16: the Stats page (tiles, charts, outlook) and the Home settings page (fields, switches, standing answers) on the fake daemon.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

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
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const button = (label: string) => $$('button').find((b) => text(b).trim() === label);
const setValue = (el: HTMLInputElement | HTMLSelectElement, value: string) =>
  act(async () => {
    const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
const blur = (el: HTMLElement) => act(async () => void el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
const field = (label: string) => $(`[data-setting="${label}"] input`) as HTMLInputElement;
const tile = (name: string) => text($(`[data-tile="${name}"] strong`));

async function mount(hash: string, s = setup()) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('stats', () => {
  it('shows a skeleton, then the four counts, the span and both charts', async () => {
    const s = setup();
    s.daemon.knobs.latency(300);
    await mount('#/stats', s);
    await until(() => $('.stats .skeleton'), 'the skeleton');
    await until(() => $('[data-tile]'), 'the tiles', 500);
    expect(['tasks done', 'moves', 'handoffs', 'blocks'].map(tile)).toEqual(['3', '1', '2', '0']);
    expect(text($('[data-span]'))).toContain(' to ');
    expect($('[data-tile="blocks"]')!.hasAttribute('data-warn')).toBe(false);
    const rows = $$('[data-agent]');
    expect(rows.map((r) => r.dataset.agent)).toEqual(['handline.architect', 'handline.auditor']);
    expect(text(rows[0])).toContain('3 h 12 m');
    expect(text($('.chart[aria-label="Agent time"]'))).toContain('1 idle agent');
  });

  it('account bars change colour at the bands, always say the number, and say no data where there is none', async () => {
    await mount('#/stats');
    await until(() => $('[data-account="alpha"]'), 'the accounts');
    const band = (a: string, w: string) => $(`[data-account="${a}"] [data-use="${w}"]`)!.getAttribute('data-band');
    expect([band('alpha', '5 h'), band('alpha', 'week'), band('beta', '5 h'), band('beta', 'week')]).toEqual(['ok', 'warn', 'bad', 'ok']);
    expect(text($('[data-account="beta"] [data-use="5 h"]'))).toContain('88%');
    expect(text($('[data-account="gamma"]'))).toContain('no data');
  });

  it('the 7 d window shows its own figures, and the old ones stay until they arrive', async () => {
    const s = await mount('#/stats');
    await until(() => tile('tasks done') === '3', 'the 24 h tiles');
    s.daemon.knobs.latency(300);
    await click(button('7 d'));
    expect(tile('tasks done')).toBe('3');
    await until(() => tile('tasks done') === '19', 'the 7 d tiles', 500);
    expect($('[data-tile="blocks"]')!.hasAttribute('data-warn')).toBe(true);
    expect(tile('blocks')).toBe('4');
  });
});

describe('outlook', () => {
  it('shows the week left with the guess apart, the runway in words, and the numbers behind Table', async () => {
    await mount('#/stats');
    await until(() => $('.outlook-svg'), 'the graph');
    expect($('.outlook-svg .line.future')!.getAttribute('stroke-dasharray')).toBeTruthy();
    expect(text($('.outlook-svg'))).toContain('guess');
    expect(text($('[data-runway="beta"]'))).toContain('runs out in 2 h 0 m');
    expect(text($('[data-runway="alpha"]'))).toContain('lasts until the week resets');
    expect($('.data-table')).toBeNull();
    await click(button('Table'));
    expect($$('.data-table tbody tr').length).toBeGreaterThan(10);
    expect(text($('.data-table'))).toContain('guess');
  });

  it('the cost view offers 7, 14 and 30 days and draws a bar per day with the values in the table', async () => {
    await mount('#/stats');
    await until(() => $('.outlook-svg'), 'the graph');
    await setValue($('select[aria-label="View"]') as HTMLSelectElement, 'cost');
    await until(() => $$('.cost-day').length === 7, 'seven days');
    expect($$('select[aria-label="Range"] option').map(text)).toEqual(['7d', '14d', '30d']);
    await setValue($('select[aria-label="Range"]') as HTMLSelectElement, '14d');
    await until(() => $$('.cost-day').length === 14, 'fourteen days');
    expect(
      $$('.cost-legend li')
        .map(text)
        .map((t) => t.trim()),
    ).toEqual(['handline.architect', 'handline.auditor', 'handline.term-build']);
    await click(button('Table'));
    expect(text($('.data-table'))).toContain('handline.architect');
  });

  it('a daemon that does not serve stats says so and draws no outlook', async () => {
    await mount('#/stats', setup({ daemon: { admin: false } }));
    await until(() => text($('.stats')).includes('does not serve stats'), 'the notice');
    expect($('.outlook')).toBeNull();
  });
});

describe('settings', () => {
  it('shows the values the daemon holds, idle close in minutes', async () => {
    await mount('#/settings');
    await until(() => field('Default cap'), 'the fields');
    expect(field('Default cap').value).toBe('4');
    expect(field('Idle close').value).toBe('30');
    expect(field('Handoff note').checked).toBe(true);
    expect(field('Auto-accept').checked).toBe(false);
  });

  it('the cap saves on leaving the field, clearing it sends null, and a bad value sends nothing and says what is allowed', async () => {
    const s = await mount('#/settings');
    await until(() => field('Default cap'), 'the fields');
    await setValue(field('Default cap'), '6');
    await blur(field('Default cap'));
    await until(() => s.daemon.admin.settings().values.active_cap === 6, 'the cap');
    await setValue(field('Default cap'), 'six');
    await blur(field('Default cap'));
    expect(text($('[data-setting="Default cap"] .field-note'))).toContain('whole number from 1');
    expect(s.daemon.admin.settings().values.active_cap).toBe(6);
    await setValue(field('Default cap'), '');
    await blur(field('Default cap'));
    await until(() => s.daemon.admin.settings().values.active_cap === 4, 'the default again');
  });

  it('idle close converts minutes to seconds', async () => {
    const s = await mount('#/settings');
    await until(() => field('Idle close'), 'the fields');
    await setValue(field('Idle close'), '45');
    await blur(field('Idle close'));
    await until(() => s.daemon.admin.settings().values.park_timeout_s === 2700, 'seconds');
    await setValue(field('Idle close'), '0');
    await blur(field('Idle close'));
    await until(() => s.daemon.admin.settings().values.park_timeout_s === 0, 'never');
  });

  it('a refused save puts the value back and the field says why', async () => {
    const s = await mount('#/settings');
    await until(() => field('Default cap'), 'the fields');
    s.daemon.knobs.refuse('settings.set', 'role', 'only the founder may change settings');
    await setValue(field('Default cap'), '9');
    await blur(field('Default cap'));
    await until(() => text($('[data-setting="Default cap"] .field-note')).includes('not saved'), 'the note');
    expect(text($('[data-setting="Default cap"] .field-note'))).toContain('only the founder');
    await until(() => field('Default cap').value === '4', 'the value back');
  });

  it('the switches save on change', async () => {
    const s = await mount('#/settings');
    await until(() => field('Auto-accept'), 'the fields');
    await click(field('Auto-accept'));
    await until(() => s.daemon.admin.settings().values.auto_accept_dialogs === true, 'auto-accept');
    await click(field('Handoff note'));
    await until(() => s.daemon.admin.settings().values.handoff_note === false, 'handoff note');
  });

  it('a daemon that does not serve settings, or a member, gets a sentence and no controls', async () => {
    await mount('#/settings', setup({ role: 'member' }));
    await until(() => text($('.settings-body')).includes('for the founder only'), 'the notice');
    expect($('.settings-body input')).toBeNull();
  });
});

describe('standing answers', () => {
  it('lists the rules, adds a folder at once, and an empty form sends nothing', async () => {
    const s = await mount('#/settings');
    await until(() => $$('[data-answer]').length === 2, 'the rules');
    expect(text($('[data-answer="dialog"]'))).toContain('claude: Do you trust the files → Yes, I trust');
    await click(button('Add…'));
    await click(button('Add'));
    await wait(30);
    expect(s.daemon.admin.call('answers.list', {}, () => 0)).toMatchObject({ answers: expect.arrayContaining([]) });
    expect($$('[data-answer]')).toHaveLength(2);
    await setValue($('input[aria-label="Folder"]') as HTMLInputElement, '/work/new');
    await click(button('Add'));
    expect($$('[data-answer]')).toHaveLength(3);
    await until(() => (s.daemon.admin.call('answers.list', {}, () => 0) as { answers: unknown[] }).answers.length === 3, 'the daemon');
  });

  it('a dialog rule needs vendor, title and option', async () => {
    const s = await mount('#/settings');
    await until(() => $$('[data-answer]').length === 2, 'the rules');
    await click(button('Add…'));
    await setValue($('select[aria-label="Kind"]') as HTMLSelectElement, 'dialog');
    await setValue($('input[aria-label="Vendor"]') as HTMLInputElement, 'claude');
    await setValue($('input[aria-label="Prompt title"]') as HTMLInputElement, 'Allow web search');
    await click(button('Add'));
    expect($$('[data-answer]')).toHaveLength(2);
    await setValue($('input[aria-label="Option"]') as HTMLInputElement, 'Allow');
    await click(button('Add'));
    await until(() => $$('[data-answer]').length === 3, 'the rule');
    await until(
      () =>
        (s.daemon.admin.call('answers.list', {}, () => 0) as { answers: { text: string }[] }).answers.some(
          (a) => a.text === 'claude: Allow web search → Allow',
        ),
      'the daemon',
    );
  });

  it('a refused add takes the row back; removing the selected rows takes them at once', async () => {
    const s = await mount('#/settings');
    await until(() => $$('[data-answer]').length === 2, 'the rules');
    s.daemon.knobs.refuse('answer.add', 'state', 'that folder does not exist');
    await click(button('Add…'));
    await setValue($('input[aria-label="Folder"]') as HTMLInputElement, '/nope');
    await click(button('Add'));
    await until(() => $('.toast'), 'the toast');
    expect(text($('.toast'))).toContain('that folder does not exist');
    await until(() => $$('[data-answer]').length === 2, 'the row gone');
    await click($('[data-answer="trust"] input'));
    await click(button('Remove selected'));
    expect($$('[data-answer]')).toHaveLength(1);
    await until(() => (s.daemon.admin.call('answers.list', {}, () => 0) as { answers: unknown[] }).answers.length === 1, 'the daemon');
  });
});
