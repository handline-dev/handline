// U16: the Usage page (outlook, accounts, who used it) and the Home settings page (fields, switches, standing answers) on the fake daemon.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';
import { ResourceRow } from '#src/MachinePage.tsx';

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
const button = (label: string) => $$('button').find((b) => text(b).trim() === label || b.title === label);
const setValue = (el: HTMLInputElement | HTMLSelectElement, value: string) =>
  act(async () => {
    const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
const blur = (el: HTMLElement) => act(async () => void el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
/** What a person does: focus the field (a late read then cannot reset the text under them), type, leave. */
const type = async (el: HTMLInputElement, value: string) => {
  await act(async () => void el.dispatchEvent(new FocusEvent('focusin', { bubbles: true })));
  await setValue(el, value);
  await blur(el);
};
const field = (label: string) => $(`[data-setting="${label}"] input`) as HTMLInputElement;
const toggle = (label: string) => $(`[data-setting="${label}"] [role="switch"]`) as HTMLElement;
const byLabel = (label: string) => ($$('label').find((l) => text(l) === label) as HTMLLabelElement).control as HTMLInputElement;

async function mount(hash: string, s = setup()) {
  location.hash = hash;
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
  vi.unstubAllGlobals();
});

describe('machine', () => {
  it('lists the resources, the agents and the storage, with no remove button', async () => {
    const s = setup();
    await mount('#/machine', s);
    await until(() => $('.machine-page [data-agent]'), 'the agents');
    expect($$('.machine-page nav button').map(text)).toEqual(['Resources', 'Agents', 'Storage']);
    expect($$('.machine-page [data-agent]').length).toBeGreaterThan(0);
    expect(button('Delete') ?? button('Remove')).toBeUndefined();
  });
  it('has a Stop on each agent row that asks before stopping, and a bar on each resource line', async () => {
    const s = setup();
    await mount('#/machine', s);
    await until(() => $('.machine-page [data-agent]'), 'the agents');
    expect($$('.machine-page [data-resource]').map((e) => e.getAttribute('data-resource'))).toEqual(['CPU', 'Memory', 'Disk']);
    const stop = $$('.machine-page [data-agent] button').find((b) => b.textContent === 'Stop')!;
    await act(async () => stop.click());
    await until(() => button('Cancel'), 'the stop sheet');
  });
});

describe('a resource line', () => {
  it('shows Low with its glyph beside the figure when the disk is low, and the bar after the figure', async () => {
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(
        <ResourceRow icon={null} label="Disk" pct={98} low>
          8 GB left
        </ResourceRow>,
      );
    });
    const row = $('[data-resource="Disk"]')!;
    expect(text(row.querySelector('[data-low]'))).toBe('Low');
    expect(row.querySelector('[data-low] svg')).not.toBeNull();
    expect(text(row).indexOf('8 GB left')).toBeLessThan(text(row).indexOf('Low'));
    expect(row.lastElementChild!.querySelector('.bg-bad')).not.toBeNull();
  });
});

describe('stats', () => {
  it('shows a skeleton, then who used it as a plain table with no tiles', async () => {
    const s = setup();
    const release = s.daemon.knobs.hold('stats.get'); // held until the skeleton is seen: no wall-clock race between the read and the skeleton's delay
    await mount('#/stats', s);
    await until(() => $('.stats .skeleton'), 'the skeleton');
    release();
    await until(() => $('.who-used'), 'the table');
    expect($('[data-tile]')).toBeNull();
    const rows = $$('.who-used [data-agent]');
    expect(rows.length).toBeGreaterThan(0);
    expect(text(rows[0])).toContain('$');
    expect($('.who-used')!.tagName).toBe('TABLE');
  });

  it('the Usage page holds the accounts one line each; the Accounts tab of Settings holds their gauges and no stats', async () => {
    await mount('#/stats');
    await until(() => $('.usage-accounts [data-account]'), 'the account lines');
    expect(text($('h2'))).toBe('Usage');
    expect($$('.usage-accounts [data-account]').length).toBeGreaterThan(0);
    expect(text($('.usage-accounts [data-account]'))).toMatch(/5-hour .*Week .*left/);
    expect($('.acct-list')).toBeNull();
    await act(async () => void (location.hash = '#/accounts'));
    await until(() => text($('h2')) === 'Settings' && $('.acct-list [data-account]'), 'the Accounts tab');
    expect($('.who-used')).toBeNull();
    expect($('.acct-list [data-account] .gauge')).not.toBeNull();
  });

  it('an account that needs a login has its state, Log in and its plan and no window text; the others show their agents in a list', async () => {
    await mount('#/stats');
    await until(() => $('.usage-accounts [data-account="gamma"]'), 'the account lines');
    const gamma = $('.usage-accounts [data-account="gamma"]')!;
    expect(text(gamma)).toContain('Login needed');
    expect(text(gamma)).not.toMatch(/left|resets|not started/);
    expect(gamma.querySelector('.badge')).toBeNull(); // a glyph and a word, no pill
    const alpha = $('.usage-accounts [data-account="alpha"]')!;
    expect(text(alpha.querySelector('.usage-state'))).toContain('In use');
    expect(alpha.querySelector('.badge')).toBeNull();
    await click(alpha.querySelector('.usage-agents button'));
    expect($$('[role="dialog"] button').length).toBeGreaterThan(0);
    expect(text($('.usage-accounts [data-account="alpha"] .usage-warn'))).not.toMatch(/ahead/);
  });

  it('the 7 d window shows its own figures, and the old ones stay until they arrive', async () => {
    const s = await mount('#/stats');
    await until(() => $('.who-used'), 'the 24 h table');
    const before = $$('.who-used [data-agent]').map(text);
    const release = s.daemon.knobs.hold('stats.get');
    await click(button('7 d'));
    expect($$('.who-used [data-agent]').map(text)).toEqual(before);
    release();
    await until(() => $$('.who-used [data-agent]').map(text).join() !== before.join(), 'the 7 d table');
  });
});

describe('outlook', () => {
  beforeEach(() => localStorage.clear());

  it('is the approved page: the readout, the line solid then dashed, now and the resets, one lost bar, no card, no legend', async () => {
    await mount('#/stats');
    await until(() => $('.outlook-svg'), 'the graph');
    expect($('.outlook.chart')).toBeNull(); // no card
    expect($('.outlook h3')).toBeNull(); // no title
    expect($('.outlook-legend')).toBeNull();
    expect(text($('.week-left'))).toMatch(/^Week \d+% left$/);
    expect(text($('.week-pace'))).toMatch(/^· at this pace about \d+% of alpha's week goes unused$/);
    expect($('.outlook-svg .line.future')!.getAttribute('stroke-dasharray')).toBeTruthy();
    expect($('.outlook-svg .line.past')!.getAttribute('stroke-dasharray')).toBeNull();
    const end = $('.outlook-svg .line.past')!.getAttribute('d')!.split(' ').at(-1);
    expect($('.outlook-svg .line.future')!.getAttribute('d')!.split(' ')[0]).toBe(end!.replace('L', 'M')); // the guess starts where the recorded line ends
    expect(text($('.now-label'))).toBe('now');
    expect($$('.y-mark').map(text)).toEqual(['0%', '50%', '100%']);
    expect($$('.x-tick').length).toBeGreaterThan(2);
    expect($$('[data-reset-mark]').map((r) => r.dataset.resetMark)).toContain('alpha');
    expect($$('[data-lost]').length).toBe(1);
    expect(text($('.lost-link'))).toMatch(/^\d+% lost ›$/); // the number is beside the bar, not only in its title
    expect($('[data-lost]')!.className).toContain('bg-acc');
    expect(text($('.usage-accounts [data-account="alpha"] .usage-lost'))).toMatch(/^\d+% lost at this pace$/);
    expect($('.usage-accounts [data-account="beta"] .usage-lost')).toBeNull(); // beta's week has no reset in the guess
  });

  it('the guess line ends at the last reset and the lost bar stands on the 0% line', async () => {
    await mount('#/stats');
    await until(() => $('.outlook-svg'), 'the graph');
    const end = $('.outlook-svg .line.future')!.getAttribute('d')!.split(' ').at(-1)!;
    expect(end.replace('L', '').split(',')[0]).toBe('560.0'); // the plot's right edge is the reset, no run on to 0%
    expect($('[data-lost]')!.className).toContain('bottom-px');
    expect(text($('[data-reset-mark="alpha"]'))).toContain('alpha');
  });

  it('cost per day stays reachable beside the range menu, with 7, 14 and 30 days, a bar per day and a click on an agent for that one alone', async () => {
    await mount('#/stats');
    await until(() => $('.outlook-svg'), 'the graph');
    await click(button('Cost per day'));
    await until(() => $$('.cost-day').length === 7, 'seven days');
    const range = $('select[aria-label="Range"]') as HTMLSelectElement;
    expect([...range.options].map((o) => o.value)).toEqual(['7d', '14d', '30d']);
    await setValue(range, '14d');
    await until(() => $$('.cost-day').length === 14, 'fourteen days');
    expect($$('.agent-cost tbody tr').map((r) => r.dataset.agent)).toEqual([
      'handline.architect',
      'handline.auditor',
      'handline.term-build',
    ]);
    expect($$('.agent-cost .cost-pick b').map(text)).toEqual(['A', 'B', 'C']);
    await click($('[data-agent="handline.auditor"] .cost-pick'));
    expect($('.cost')!.getAttribute('aria-label')).toBe('Cost per day, handline.auditor alone');
  });

  it('the range last chosen is the one it opens on next time', async () => {
    await mount('#/stats');
    await until(() => $('.outlook-svg'), 'the graph');
    await setValue($('select[aria-label="Range"]') as HTMLSelectElement, '3d');
    await until(() => $('.outlook-svg'), 'the graph');
    expect(JSON.parse(localStorage.getItem('outlook-look')!)).toEqual({ view: 'left', range: '3d' });
  });

  it('pointing at the graph reads the time and the percent, and leaving clears it', async () => {
    await mount('#/stats');
    await until(() => $('.outlook-svg'), 'the graph');
    expect(text($('.outlook-readout'))).toBe('');
    const plot = $('.outlook-plot')!;
    vi.spyOn($('.outlook-svg')!, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      width: 560,
      height: 160,
      right: 560,
      bottom: 160,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    await act(async () => void plot.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 100 })));
    expect(text($('.outlook-readout'))).toMatch(/\d+% left/);
    expect($('.outlook-svg .cursor')).not.toBeNull();
    expect(($('select[aria-label="Range"]') as HTMLSelectElement).value).toBe('7d'); // the week is what it opens on
    await act(async () => void plot.dispatchEvent(new MouseEvent('pointerout', { bubbles: true })));
    await act(async () => void plot.dispatchEvent(new MouseEvent('pointerleave')));
    expect(text($('.outlook-readout'))).toBe('');
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
    expect(field('Close idle sessions after').value).toBe('30');
    await click($$('button[aria-expanded]').find((b) => text(b).includes('Advanced'))!);
    await until(() => toggle('Handoff note'), 'the Advanced rows');
    expect(toggle('Handoff note').getAttribute('aria-checked')).toBe('true');
  });

  it('the cap saves on leaving the field, clearing it sends null, and a bad value sends nothing and says what is allowed', async () => {
    const s = await mount('#/settings');
    await until(() => field('Default cap'), 'the fields');
    await setValue(field('Default cap'), '6');
    await blur(field('Default cap'));
    await until(() => s.daemon.admin.settings().values.active_cap === 6, 'the cap');
    await setValue(field('Default cap'), 'six');
    await blur(field('Default cap'));
    expect(text($('[data-setting="Default cap"] .ui-note'))).toContain('whole number from 1');
    expect(s.daemon.admin.settings().values.active_cap).toBe(6);
    await setValue(field('Default cap'), '');
    await blur(field('Default cap'));
    await until(() => s.daemon.admin.settings().values.active_cap === 4, 'the default again');
  });

  it('idle close converts minutes to seconds', async () => {
    const s = await mount('#/settings');
    await until(() => field('Close idle sessions after'), 'the fields');
    await type(field('Close idle sessions after'), '45');
    await until(() => s.daemon.admin.settings().values.park_timeout_s === 2700, 'seconds');
    await type(field('Close idle sessions after'), '0');
    await until(() => s.daemon.admin.settings().values.park_timeout_s === 0, 'never');
  });

  it('a refused save puts the value back and the field says why', async () => {
    const s = await mount('#/settings');
    await until(() => field('Default cap'), 'the fields');
    s.daemon.knobs.refuse('settings.set', 'role', 'only the founder may change settings');
    await setValue(field('Default cap'), '9');
    await blur(field('Default cap'));
    await until(() => text($('[data-setting="Default cap"] .ui-note')).includes('not saved'), 'the note');
    expect(text($('[data-setting="Default cap"] .ui-note'))).toContain('only the founder');
    await until(() => field('Default cap').value === '4', 'the value back');
  });

  it('a switch saves on change', async () => {
    const s = await mount('#/settings');
    const advanced = () => $$('button[aria-expanded]').find((b) => text(b).includes('Advanced'));
    await until(advanced, 'the Advanced fold');
    await click(advanced());
    await until(() => toggle('Handoff note'), 'the Advanced rows');
    await click(toggle('Handoff note'));
    await until(() => s.daemon.admin.settings().values.handoff_note === false, 'handoff note');
  });

  it('the title and the tab strip are the same, in the same place, on all four tabs', async () => {
    const head = () => {
      const strip = $('[role=tablist]');
      return {
        title: text($('h2')),
        tabs: $$('[role=tab]').map((t) => text(t).replace(/\s*\d+$/, '')),
        parent: strip?.parentElement?.parentElement?.tagName,
      };
    };
    await mount('#/settings');
    await until(() => $('[role=tablist]'), 'the tab strip');
    const first = head();
    expect(first.title).toBe('Settings');
    expect(first.tabs.slice(0, 3)).toEqual(['General', 'Accounts', 'Team']);
    for (const hash of ['#/accounts', '#/team', '#/team/hr']) {
      await act(async () => void (location.hash = hash));
      await until(() => $('[role=tablist]') && head().parent, hash);
      expect(head()).toEqual(first);
      expect($('.topbar header [role=tablist]')).not.toBeNull();
    }
  });

  it('Advanced and About stay folded; opened, Advanced saves its rows and About holds the connection, Admin, Debug and Seal', async () => {
    const s = await mount('#/settings');
    await until(() => field('Default cap'), 'the fields');
    expect($(`[data-setting="Pace target"]`)).toBeNull();
    expect(button('Admin')).toBeUndefined();
    const fold = (name: string) => $$('button[aria-expanded]').find((b) => text(b).includes(name))!;
    await click(fold('Advanced'));
    await until(() => field('Pace target'), 'the Advanced rows');
    await setValue(field('Pace target'), '90');
    await blur(field('Pace target'));
    await until(() => s.daemon.admin.settings().values.pace_target_pct === 90, 'the pace target');
    await click(fold('About'));
    await until(() => button('Admin'), 'the About rows');
    expect(button('Debug: terminals')).toBeDefined();
    expect(text(document.body)).toContain('Connection');
  });

  it('lists the paired devices and revokes one only after a confirm', async () => {
    await mount('#/settings');
    await until(() => $('[data-device="d2"]'), 'the devices');
    expect(text($('[data-device="d1"]'))).toContain('connected');
    await click($('[title="Revoke phone-3b54"]')!);
    expect($('[data-device="d2"]')).not.toBeNull(); // nothing yet: the confirm comes first
    await click($('.ui-dialog .ui-btn.solid')!);
    await until(() => !$('[data-device="d2"]'), 'the device gone');
    expect($('[data-device="d1"]')).not.toBeNull();
  });

  it('a revoke the daemon refuses keeps the ask open with its reason and says nothing was revoked', async () => {
    const s = setup();
    await mount('#/settings', s);
    await until(() => $('[data-device="d2"]'), 'the devices');
    await click($('[title="Revoke phone-3b54"]')!);
    (s.daemon.admin as unknown as { devices: unknown[] }).devices = []; // the daemon no longer knows the device: it refuses the revoke
    await click($('.ui-dialog .ui-btn.solid')!);
    await until(() => $('.ui-dialog [role="alert"]'), 'the reason');
    expect(text($('.ui-dialog [role="alert"]'))).toContain('no such device');
    expect(text(document.body)).not.toContain('Revoked phone-3b54');
  });

  it('Computers: this computer is the first server, and Connect a device shows the invite as a QR and a link that works once', async () => {
    const s = setup();
    s.daemon.admin.tailscaleHost = 'mac-studio.tail1.ts.net';
    await mount('#/settings', s);
    await until(() => text($('[data-server="this"]')).includes('mac-studio'), 'the server row with its host name');
    expect(text($('[data-server="this"]'))).toContain('This computer');
    expect(text(document.body)).toContain('Servers run agents. Devices only connect.');
    await click(button('Connect a device')!);
    await until(() => $('.ui-dialog [aria-label="Invite QR code"] svg'), 'the QR');
    const dialog = $('.ui-dialog')!;
    expect(text(dialog)).toMatch(/https?:\/\/mac\.tail1\.ts\.net:7443\/#pair=[A-Z0-9-]+/);
    expect(text(dialog)).toMatch(/Valid for\s*[45]:\d\d/);
    expect(text(dialog)).toContain('Anyone with this link for 5 minutes gets full control');
  });

  it('a number shows its unit as a suffix inside the field', async () => {
    await mount('#/settings');
    await until(() => field('Close idle sessions after'), 'the fields');
    expect(text($('[data-setting="Close idle sessions after"] .ui-input-wrap .ui-unit'))).toBe('min');
    expect(text($('[data-setting="Default cap"] .ui-input-wrap .ui-unit'))).toBe('agents');
  });

  it('a daemon that does not serve settings, or a member, gets a sentence and no controls', async () => {
    await mount('#/settings', setup({ role: 'member' }));
    await until(() => text($('.settings-body')).includes('for the founder only'), 'the notice');
    expect($('.settings-body input, .settings-body [role="switch"]')).toBeNull();
  });
});

describe('standing answers', () => {
  it('lists the rules, adds a folder at once, and an empty form sends nothing', async () => {
    const s = await mount('#/settings');
    await until(() => $$('[data-answer]').length === 2, 'the rules');
    expect(text($('[data-answer="dialog"]'))).toContain('When claude asks "Do you trust the files", choose "Yes, I trust"');
    await click(button('Add'));
    await click(button('Add'));
    await wait(30);
    expect(s.daemon.admin.call('answers.list', {}, () => 0)).toMatchObject({ answers: expect.arrayContaining([]) });
    expect($$('[data-answer]')).toHaveLength(2);
    await setValue(byLabel('Folder') as HTMLInputElement, '/work/new');
    await click(button('Add'));
    expect($$('[data-answer]')).toHaveLength(3);
    await until(() => (s.daemon.admin.call('answers.list', {}, () => 0) as { answers: unknown[] }).answers.length === 3, 'the daemon');
  });

  it('a dialog rule needs vendor, title and option', async () => {
    const s = await mount('#/settings');
    await until(() => $$('[data-answer]').length === 2, 'the rules');
    await click(button('Add'));
    await setValue($('select[aria-label="Kind"]') as HTMLSelectElement, 'dialog');
    await setValue(byLabel('Vendor') as HTMLInputElement, 'claude');
    await setValue(byLabel('Prompt title') as HTMLInputElement, 'Allow web search');
    await click(button('Add'));
    expect($$('[data-answer]')).toHaveLength(2);
    await setValue(byLabel('Option') as HTMLInputElement, 'Allow');
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

  it('a refused add takes the row back; its trash icon takes a row at once', async () => {
    const s = await mount('#/settings');
    await until(() => $$('[data-answer]').length === 2, 'the rules');
    s.daemon.knobs.refuse('answer.add', 'state', 'that folder does not exist');
    await click(button('Add'));
    await setValue(byLabel('Folder') as HTMLInputElement, '/nope');
    await click(button('Add'));
    await until(() => $('.toast'), 'the toast');
    expect(text($('.toast'))).toContain('that folder does not exist');
    await until(() => $$('[data-answer]').length === 2, 'the row gone');
    await click($('[data-answer="trust"] button'));
    await until(() => $$('[data-answer]').length === 1, 'the row gone');
    await until(() => (s.daemon.admin.call('answers.list', {}, () => 0) as { answers: unknown[] }).answers.length === 1, 'the daemon');
  });
});

describe('admin page', () => {
  it('says what the daemon does, why agents wake, how full memory is and what just happened', async () => {
    await mount('#/admin');
    await until(() => $('.admin-memory [data-agent]'), 'the memory rows');
    expect(text($('.admin-daemon'))).toContain('Running since');
    expect(text($('.admin-wakes'))).toContain('wakes: a task arrived');
    expect(text($('.admin-memory'))).toContain('25% full');
    await until(() => $('.admin-events li'), 'the events');
    expect(text($('.admin-events'))).toContain('Task T7 done');
  });
});
