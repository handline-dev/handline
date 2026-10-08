// The phone's Stats tab on the fake daemon: the counts and outlook with their ranges, the settings that save and the one that is refused,
// the standing answers, and the skeleton, empty, error, pending and not-served states.
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { $, box, mount as mountHome, press, until } from './harness.tsx';

const text = (el: Element | null = box) => el?.textContent ?? '';
const type = (id: string, value: string) =>
  act(async () => {
    const el = $(id) as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

async function mount(opts: { admin?: boolean; latency?: number; role?: 'member' } = {}) {
  const m = await mountHome((s) => opts.latency && s.daemon.knobs.latency(opts.latency), { admin: opts.admin }, undefined, {
    role: opts.role,
  });
  await press($('nav-stats'));
  return m;
}

const page = (name: 'stats' | 'settings') => press($(`page-${name}`));

describe('the stats tab', () => {
  it('shows a member the stats and says only the founder changes settings, with no settings page to open', async () => {
    const m = await mount({ role: 'member' });
    await until(() => $('tile-tasks_done'), 'the tiles');
    expect($('settings-founder-only')).not.toBeNull();
    expect(text()).toContain('Only the founder changes settings.');
    expect($('page-settings')).toBeNull();
    expect($('settings-page')).toBeNull();
    m.store.stop();
  });

  it('shows the four counts and each agent’s time, and the week’s figures after the window changes', async () => {
    const m = await mount();
    await until(() => $('tile-tasks_done'), 'the tiles');
    expect(text($('tile-tasks_done'))).toContain('3');
    expect(text($('time-handline.architect'))).toContain('3 h 12 m');
    await press($('window-7d'));
    await until(() => text($('tile-tasks_done')).includes('19'), 'the week');
    expect(text($('tile-blocks'))).toContain('4');
    m.store.stop();
  });

  it('draws the week left with each account’s runway, and the cost per day for ranges to 30 d', async () => {
    const m = await mount();
    await until(() => $('outlook-left'), 'the week left');
    expect(text($('runway-beta'))).toContain('runs out in 2 h');
    expect(text($('runway-alpha'))).toContain('lasts until the week resets');
    await press($('view-cost'));
    await press($('range-30d'));
    await until(() => $('outlook-cost')?.children.length === 30, 'thirty days of cost');
    expect(text($('cost-agent-handline.architect'))).toContain('$');
    m.store.stop();
  });

  it('shows a skeleton while the first read is out', async () => {
    const m = await mount({ latency: 1500 });
    expect(box.querySelector('[data-testid="tile-tasks_done"]')).toBeNull();
    await until(() => $('skeleton-stats'), 'the skeleton');
    await until(() => $('tile-tasks_done'), 'the tiles', 6000);
    m.store.stop();
  });

  it('says why when stats are refused, and retries', async () => {
    const m = await mount();
    await until(() => $('tile-tasks_done'), 'the tiles');
    m.daemon.knobs.refuse('stats.get', 'state', 'resting', 'the daemon is resting');
    await press($('window-7d'));
    await until(() => $('error'), 'the error');
    expect(text($('error'))).toContain('the daemon is resting');
    m.store.stop();
  });

  it('says this daemon does not serve what it does not list', async () => {
    const m = await mount({ admin: false });
    await until(() => $('not-served'), 'the notice');
    expect(text()).toContain('This daemon does not serve stats.');
    expect(text()).toContain('This daemon does not serve the outlook.');
    await page('settings');
    expect(text()).toContain('This daemon does not serve settings.');
    expect(text()).toContain('This daemon does not serve standing answers.');
    m.store.stop();
  });
});

describe('the settings', () => {
  it('shows the values and saves the cap and idle close in the daemon’s units', async () => {
    const m = await mount();
    await page('settings');
    await until(() => $('cap-input'), 'the settings');
    expect(($('cap-input') as HTMLInputElement).value).toBe('4');
    expect(($('idle-input') as HTMLInputElement).value).toBe('30');
    await type('cap-input', '2');
    await press($('cap-save'));
    await type('idle-input', '10');
    await press($('idle-save'));
    await until(
      () => m.daemon.admin.settings().values.active_cap === 2 && m.daemon.admin.settings().values.park_timeout_s === 600,
      'both saves',
    );
    m.store.stop();
  });

  it('refuses an entry that is not a number without sending it', async () => {
    const m = await mount();
    await page('settings');
    await until(() => $('cap-input'), 'the settings');
    await type('cap-input', 'many');
    await press($('cap-save'));
    expect(text($('cap-note'))).toContain('a whole number from 1');
    expect(m.daemon.admin.settings().values.active_cap).toBe(4);
    m.store.stop();
  });

  it('puts the field back with the daemon’s reason when a save is refused', async () => {
    const m = await mount();
    await page('settings');
    await until(() => $('cap-input'), 'the settings');
    m.daemon.knobs.refuse('settings.set', 'role', 'only the founder may change settings');
    await type('cap-input', '2');
    await press($('cap-save'));
    await until(() => text($('cap-note')).includes('only the founder'), 'the refusal');
    expect(($('cap-input') as HTMLInputElement).value).toBe('4');
    m.store.stop();
  });

  it('flips a switch at once and shows saving while the daemon is slow', async () => {
    const m = await mount();
    await page('settings');
    await until(() => $('auto-switch'), 'the switch');
    m.daemon.knobs.latency(600);
    await press($('auto-switch'));
    await until(() => text($('auto-switch')).includes('On'), 'the switch to show On before the daemon answers', 400);
    await until(() => text($('auto-note')).includes('saving'), 'the saving note', 1000);
    await until(() => m.daemon.admin.settings().values.auto_accept_dialogs === true, 'the save', 3000);
    m.store.stop();
  });
});

describe('the standing answers', () => {
  it('adds a folder and a dialog answer, and removes one', async () => {
    const m = await mount();
    await page('settings');
    await until(() => $('answer-trust'), 'the answers');
    expect(box.querySelectorAll('[data-testid="answer-trust"]').length).toBe(1);
    await press($('answer-new'));
    await type('answer-folder', '/work/new');
    await press($('answer-add'));
    await until(() => box.querySelectorAll('[data-testid="answer-trust"]').length === 2, 'the folder');
    await press($('answer-new'));
    await press($('kind-dialog'));
    await type('answer-vendor', 'claude');
    await type('answer-title', 'Continue');
    await type('answer-option', 'Yes');
    await press($('answer-add'));
    await until(() => box.querySelectorAll('[data-testid="answer-dialog"]').length === 2, 'the dialog answer');
    await press(box.querySelector('[data-testid="answer-trust"] [data-testid="answer-remove"]'));
    await until(() => box.querySelectorAll('[data-testid="answer-trust"]').length === 1, 'the removal');
    m.store.stop();
  });

  it('says why when an add or a remove is refused, and the list stays as the daemon has it', async () => {
    const m = await mount();
    await page('settings');
    await until(() => $('answer-trust'), 'the answers');
    m.daemon.knobs.refuse('answer.add', 'role', 'only the founder may add an answer');
    await press($('answer-new'));
    await type('answer-folder', '/work/refused');
    await press($('answer-add'));
    await until(() => text($('answers-note')).includes('only the founder may add'), 'the add refusal');
    await until(() => box.querySelectorAll('[data-testid="answer-trust"]').length === 1, 'the list back as it was');
    m.daemon.knobs.refuse('answer.remove', 'role', 'only the founder may remove an answer');
    await press(box.querySelector('[data-testid="answer-trust"] [data-testid="answer-remove"]'));
    await until(() => text($('answers-note')).includes('only the founder may remove'), 'the remove refusal');
    await until(() => box.querySelectorAll('[data-testid="answer-trust"]').length === 1, 'the row back');
    m.store.stop();
  });

  it('says none are set when the list is empty', async () => {
    const m = await mount();
    await page('settings');
    await until(() => $('answer-trust'), 'the answers');
    for (let n = 3; n > 0; n--) {
      const b = box.querySelector<HTMLElement>('[data-testid="answer-remove"]');
      if (b) await press(b);
    }
    await until(() => text($('answers')).includes('None. You are asked'), 'the empty sentence');
    m.store.stop();
  });

  it('keeps a form’s text out of the daemon until it is complete', async () => {
    const m = await mount();
    await page('settings');
    await until(() => $('answer-trust'), 'the answers');
    await press($('answer-new'));
    await press($('answer-add'));
    expect(box.querySelectorAll('[data-testid="answer-trust"]').length).toBe(1);
    m.store.stop();
  });
});
