// The phone's Settings rows the web has: the machine load limits, the Background rows, the notice batch and the handoff prompt.
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { INVALID } from '@handline/app-core';
import { parsePrompt } from '#src/screens/Settings.tsx';
import { $, box, mount, press, until } from '#test/harness.tsx';

const text = (el: Element | null = box) => el?.textContent ?? '';
const type = (id: string, value: string) =>
  act(async () => {
    const el = $(id) as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

async function settings(prepare?: Parameters<typeof mount>[0]) {
  const m = await mount(prepare, { admin: true });
  await press($('nav-agents'));
  await press($('agents-menu'));
  await press($('settings-open'));
  await until(() => $('cap-input'), 'the settings');
  return m;
}
const values = (m: Awaited<ReturnType<typeof settings>>) => m.daemon.admin.settings().values;

describe('the settings rows the web has', () => {
  it('shows the fake’s values and sends one key per save', async () => {
    const m = await settings();
    const v = values(m);
    const rows = {
      'host-cap': ['host_active_cap', '3'],
      load: ['load_per_core_pct', '200'],
      ram: ['ram_free_min_pct', '0'],
      'bg-notice': ['bg_notice_s', '60'],
      'bg-grace': ['bg_grace_s', '30'],
      'bg-check': ['bg_check_s', '90'],
      digest: ['notice_digest_s', '45'],
    } as const;
    for (const [id, [key]] of Object.entries(rows)) expect(($(`${id}-input`) as HTMLInputElement).value).toBe(String(v[key]));
    for (const [id, [, next]] of Object.entries(rows)) {
      await type(`${id}-input`, next);
      await press($(`${id}-save`));
    }
    await until(() => Object.values(rows).every(([key, next]) => String(values(m)[key]) === next), 'every save');
    expect(values(m).active_cap).toBe(v.active_cap);
    m.store.stop();
  });

  it('sets pacing with one key per change and keeps the pace numbers in range', async () => {
    const m = await settings();
    const v = values(m);
    expect(($('pace-target-input') as HTMLInputElement).value).toBe(String(v.pace_target_pct));
    expect(($('pace-burst-input') as HTMLInputElement).value).toBe(String(v.pace_burst_pct));
    await press($('pacing-switch'));
    await until(() => values(m).pacing === false, 'pacing off');
    for (const [id, bad] of [
      ['pace-target', '40'],
      ['pace-target', '101'],
      ['pace-burst', '51'],
      ['pace-burst', '1.5'],
    ] as const) {
      await type(`${id}-input`, bad);
      await press($(`${id}-save`));
      expect(text($(`${id}-note`))).toContain('a whole number from');
    }
    expect(values(m).pace_target_pct).toBe(v.pace_target_pct);
    expect(values(m).pace_burst_pct).toBe(v.pace_burst_pct);
    await type('pace-target-input', '90');
    await press($('pace-target-save'));
    await type('pace-burst-input', '0');
    await press($('pace-burst-save'));
    await until(() => values(m).pace_target_pct === 90 && values(m).pace_burst_pct === 0, 'both saves');
    expect(values(m).pacing).toBe(false);
    expect(values(m).active_cap).toBe(v.active_cap);
    m.store.stop();
  });

  it('does not send a bad number', async () => {
    const m = await settings();
    const before = values(m).load_per_core_pct;
    await type('load-input', '1.5');
    await press($('load-save'));
    expect(text($('load-note'))).toContain('a whole number');
    expect(values(m).load_per_core_pct).toBe(before);
    m.store.stop();
  });

  it('sends the handoff prompt, not a line break, and says when {path} is missing', async () => {
    const m = await settings();
    expect(($('prompt-input') as HTMLInputElement).value).toBe(values(m).handoff_prompt);
    expect(text($('setting-prompt'))).not.toContain('Without {path}');
    expect(parsePrompt('one\ntwo')).toBe(INVALID); // the input drops a break itself; a paste on a phone may not
    expect(parsePrompt(' one ')).toBe('one');
    await type('prompt-input', 'Write your note now, please');
    await press($('prompt-save'));
    await until(() => values(m).handoff_prompt === 'Write your note now, please', 'the save');
    await until(() => text($('setting-prompt')).includes('Without {path}'), 'the path note');
    m.store.stop();
  });

  it('shows no rows on a daemon without settings.get', async () => {
    const m = await mount((s) => s.daemon.knobs.unserve('settings.get'), { admin: true });
    await press($('nav-agents'));
    await press($('agents-menu'));
    await press($('settings-open'));
    await until(() => text().includes('does not serve settings'), 'the note');
    expect($('load-input')).toBeNull();
    expect($('prompt-input')).toBeNull();
    m.store.stop();
  });
});
