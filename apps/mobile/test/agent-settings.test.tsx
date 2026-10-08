// The phone's agent settings and schedules on the fake daemon: each field saves on its own, a refusal puts the value back with its reason,
// the two danger switches ask first, a member sees the page read-only, and schedules add, edit and remove for the founder.
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import type { setup } from '@handline/fake-daemon/helpers';
import { AgentSettings } from '../src/screens/AgentSettings.tsx';
import { $, ARCH, box, mount as mountHarness, press, until } from './harness.tsx';

const text = (el: Element | null = box) => el?.textContent ?? '';
const type = (id: string, value: string) =>
  act(async () => {
    const el = $(id) as HTMLInputElement | HTMLTextAreaElement;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

const screen = () => <AgentSettings agent={ARCH} onBack={() => {}} />;

function mount(
  opts: { role?: 'founder' | 'member' | 'agent'; latency?: number } = {},
  prepare: (s: ReturnType<typeof setup>) => void = () => {},
) {
  return mountHarness(
    (s) => {
      if (opts.latency) s.daemon.knobs.latency(opts.latency);
      prepare(s);
    },
    undefined,
    undefined,
    { role: opts.role, screen },
  );
}
const stored = (m: Awaited<ReturnType<typeof mount>>) => m.daemon.sessions.get(ARCH)!.settings;
/** react-native-web draws no aria-selected or aria-checked: a chosen option is the bold one, a switch says On or Off. */
const chosen = (id: string) => ($(id)?.firstElementChild as HTMLElement | null)?.style.fontWeight === '700';
const input = (id: string) => $(id) as HTMLInputElement;

describe('agent settings', () => {
  it('shows each field with the stored value and the account the session runs on', async () => {
    const m = await mount();
    await until(() => $('model-input'), 'the settings');
    expect(input('model-input').value).toBe('opus');
    expect(input('context_k-input').value).toBe('300');
    expect(chosen('effort-high')).toBe(true);
    expect(text($('sandbox-switch'))).toBe('On');
    expect(text($('skip_permissions-switch'))).toBe('Off');
    for (const k of ['weight', 'handoff_note', 'repo', 'mode', 'browser', 'browser_sites', 'idle_close'])
      expect($(`field-${k}`)).not.toBeNull();
    expect($('session-account')).not.toBeNull();
    m.store.stop();
  });

  it('a choice saves on a tap and the daemon holds it', async () => {
    const m = await mount();
    await until(() => $('effort-max'), 'the settings');
    await press($('effort-max'));
    await until(() => stored(m).effort === 'max', 'the save');
    await until(() => chosen('effort-max'), 'the choice to show');
    m.store.stop();
  });

  it('a number saves on Save; one that is not a number is not sent and its note says what is allowed', async () => {
    const m = await mount();
    await until(() => $('context_k-input'), 'the settings');
    await type('context_k-input', 'abc');
    await press($('context_k-save'));
    expect(text($('context_k-note'))).toContain('whole number');
    expect(stored(m).context_k).toBe('300');
    await type('context_k-input', '500');
    await press($('context_k-save'));
    await until(() => stored(m).context_k === '500', 'the save');
    expect($('context_k-note')).toBeNull();
    m.store.stop();
  });

  it("a refused save puts the value back and the field's note gives the daemon's reason", async () => {
    const m = await mount();
    await until(() => $('effort-max'), 'the settings');
    m.daemon.knobs.refuse('agent.set', 'state', 'The daemon is busy; try again.');
    await press($('effort-max'));
    await until(() => text($('effort-note')).includes('busy'), 'the note');
    expect(chosen('effort-high')).toBe(true);
    expect(stored(m).effort).toBe('high');
    m.store.stop();
  });

  it('skip prompts asks first in the bridge words; Cancel sends nothing and Turn on saves', async () => {
    const m = await mount();
    await until(() => $('skip_permissions-switch'), 'the settings');
    await press($('skip_permissions-switch'));
    expect(text($('skip_permissions-ask'))).toContain('it will act without asking, from the next launch');
    await press($('skip_permissions-cancel'));
    expect(stored(m).skip_permissions).toBe('off');
    await press($('skip_permissions-switch'));
    await press($('skip_permissions-go'));
    await until(() => stored(m).skip_permissions === 'on', 'the save');
    m.store.stop();
  });

  it('turning the sandbox off asks; turning it back on asks nothing', async () => {
    const m = await mount();
    await until(() => $('sandbox-switch'), 'the settings');
    await press($('sandbox-switch'));
    expect(text($('sandbox-ask'))).toContain('nothing holding them back');
    await press($('sandbox-go'));
    await until(() => stored(m).sandbox === 'off', 'off');
    await press($('sandbox-switch'));
    expect($('sandbox-ask')).toBeNull();
    await until(() => stored(m).sandbox === 'on', 'on again');
    m.store.stop();
  });

  it('sites are added and removed as a space-separated list', async () => {
    const m = await mount();
    await until(() => $('site-input'), 'the settings');
    await type('site-input', 'https://a.example');
    await press($('site-add'));
    await until(() => stored(m).browser_sites === 'https://a.example', 'added');
    await until(() => $('site-remove'), 'the chip');
    await press($('site-remove'));
    await until(() => stored(m).browser_sites === '', 'removed');
    m.store.stop();
  });

  it('a member sees the page read-only with no schedule controls', async () => {
    const m = await mount({ role: 'member' });
    await until(() => $('model-input'), 'the settings');
    expect(text()).toContain('Only the founder edits agent settings.');
    expect(input('model-input').readOnly).toBe(true);
    expect($('schedule-new')).toBeNull();
    m.store.stop();
  });

  it('shows a skeleton while the first read is out', async () => {
    const m = await mount({ latency: 1500 });
    await until(() => $('skeleton-settings'), 'the skeleton');
    await until(() => $('model-input'), 'the settings', 6000);
    m.store.stop();
  });

  it('says why when the agent cannot be read, and retries', async () => {
    const m = await mount({}, (s) => s.daemon.knobs.refuse('agent.get', 'state', 'the daemon is resting'));
    await until(() => $('error'), 'the error');
    expect(text($('error'))).toContain('the daemon is resting');
    m.store.stop();
  });

  it('says this daemon does not serve schedules when it refuses the list as unsupported', async () => {
    const m = await mount({}, (s) =>
      s.daemon.knobs.refuse('schedules.list', 'unsupported', 'the fake daemon does not serve schedules.list'),
    );
    await until(() => $('not-served'), 'the notice');
    expect(text($('schedules'))).toContain('This daemon does not serve schedules.');
    m.store.stop();
  });
});

describe('schedules', () => {
  it('adds a schedule shown in words with its cron, edits it, and removes it after asking', async () => {
    const m = await mount();
    await until(() => $('schedule-new'), 'the schedules');
    await until(() => text($('schedules')).includes('No schedules for this agent.'), 'the empty sentence');
    await press($('schedule-new'));
    await type('schedule-cron', '0 9 * * 1-5');
    await type('schedule-message', 'Check the board');
    expect(text($('schedule-form'))).toContain('every weekday 09:00');
    await press($('schedule-save'));
    await until(() => text($('schedules')).includes('Check the board'), 'the line');
    expect(text($('schedules'))).toContain('every weekday 09:00');
    await until(() => (m.daemon as unknown as { script: { schedules: unknown[] } }).script.schedules.length === 1, 'the daemon has it');
    await press($('schedule-edit'));
    await type('schedule-message', 'Check the board twice');
    await press($('schedule-save'));
    await until(() => text($('schedules')).includes('twice'), 'edited');
    await press($('schedule-remove'));
    await press($('schedule-remove-no'));
    expect(text($('schedules'))).toContain('twice');
    await press($('schedule-remove'));
    await press($('schedule-remove-yes'));
    await until(() => text($('schedules')).includes('No schedules'), 'removed');
    m.store.stop();
  });

  it('a form without a when or a message sends nothing', async () => {
    const m = await mount();
    await until(() => $('schedule-new'), 'the schedules');
    await press($('schedule-new'));
    await press($('schedule-save'));
    expect(text($('schedules-note'))).toContain('needs a when and a message');
    m.store.stop();
  });

  it('a refused add reverts and says why', async () => {
    const m = await mount();
    await until(() => $('schedule-new'), 'the schedules');
    m.daemon.knobs.refuse('schedule.add', 'role', 'Only the founder keeps schedules.');
    await press($('schedule-new'));
    await type('schedule-cron', '0 9 * * *');
    await type('schedule-message', 'x');
    await press($('schedule-save'));
    await until(() => text($('schedules-note')).includes('Only the founder'), 'the reason');
    expect(box.querySelectorAll('[data-testid="schedule-remove"]').length).toBe(0);
    expect(text($('schedules'))).not.toContain('0 9 * * *');
    m.store.stop();
  });

  it('a refused remove keeps the line and says why', async () => {
    const m = await mount();
    await until(() => $('schedule-new'), 'the schedules');
    await press($('schedule-new'));
    await type('schedule-cron', '0 9 * * *');
    await type('schedule-message', 'keep me');
    await press($('schedule-save'));
    await until(() => $('schedule-remove'), 'the line');
    await until(() => (m.daemon as unknown as { script: { schedules: unknown[] } }).script.schedules.length === 1, 'the daemon has it');
    m.daemon.knobs.refuse('schedule.remove', 'role', 'Only the founder removes schedules.');
    await press($('schedule-remove'));
    await press($('schedule-remove-yes'));
    await until(() => text($('schedules-note')).includes('Only the founder removes'), 'the reason');
    await until(() => text($('schedules')).includes('keep me'), 'the line back');
    m.store.stop();
  });

  it('shows saving while the daemon is slow', async () => {
    const m = await mount();
    await until(() => $('schedule-new'), 'the schedules');
    await press($('schedule-new'));
    await type('schedule-cron', '30 8 * * *');
    await type('schedule-message', 'slow');
    m.daemon.knobs.latency(600);
    await press($('schedule-save'));
    await until(() => $('schedules-pending'), 'the saving note', 1000);
    m.store.stop();
  });

  it('says this daemon does not serve agent settings when hello does not list agent.set', async () => {
    const m = await mount({}, (s) => s.daemon.unservedMethods.add('agent.set'));
    await until(() => text().includes('This daemon does not serve agent settings.'), 'the sentence');
    expect(input('model-input').readOnly).toBe(true);
    m.store.stop();
  });
});
