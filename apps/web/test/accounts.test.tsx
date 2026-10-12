// U14: the Accounts page and the usage popup on the fake daemon's accounts: skeleton, cards with gauges and state chips, the popup, one account's
// tabs, settings that save and revert, login that opens no credential field, and a daemon that does not serve accounts.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { second, setup } from '@handline/fake-daemon/helpers';
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
const memory = (rail: string): KV => {
  const data = new Map<string, string>([['rail', rail]]);
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const button = (label: string) => $$('button, a').find((b) => text(b).trim().startsWith(label));
const card = (name: string) => $(`[data-account="${name}"]`)!;
const setValue = (el: HTMLInputElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const blur = (el: HTMLElement) => act(async () => void el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));

async function mount(hash = '#/accounts', s = setup(), rail = '0') {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory(rail)} />);
  });
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

// Seeded: alpha 41% / 62% with the architect on it; beta (alias "review") 88% / 30% with the auditor; gamma needs login.
describe('the list', () => {
  it('shows the skeleton while loading, then a line per account with its gauges, state chip and agents', async () => {
    const s = setup();
    s.daemon.knobs.latency(400);
    await mount('#/accounts', s);
    await until(() => $('.accounts .skeleton'), 'the skeleton');
    expect($('.accounts .skeleton')!.getAttribute('aria-busy')).toBe('true');
    await until(() => $$('.acct-row').length === 3, 'the rows', 600);
    expect($('.accounts .skeleton')).toBeNull();
    expect(text(card('alpha').querySelector('.acct-state'))).toBe('in use');
    expect(text(card('beta').querySelector('strong'))).toBe('review');
    expect(text(card('gamma').querySelector('.acct-state'))).toBe('login needed');
    expect(text(card('alpha').querySelector('[data-gauge="5 h"] .gauge-pct'))).toBe('41%');
    expect(card('alpha').querySelector('[data-gauge="5 h"]')!.getAttribute('title')).toMatch(/^resets in 2 h/);
    expect(text(card('alpha').querySelector('.acct-agents'))).toContain('1/4');
    expect(card('alpha').querySelector('.acct-agents')!.getAttribute('title')).toContain('1 / cap 4');
    expect(text(card('gamma'))).toContain('no reading');
  });

  it('says on each line the login, the plan, when the week resets, and whether it can take another agent and why not', async () => {
    await mount();
    await until(() => text(card('beta').querySelector('[data-usage]')).includes('cannot'), 'the usage line');
    expect(text(card('alpha').querySelector('[data-usage]'))).toMatch(/^logged in · pro · week resets .*· can take another agent$/);
    expect(text(card('beta').querySelector('[data-usage]'))).toContain('cannot take another agent: 5h 88% at last reading');
    expect(text(card('gamma').querySelector('[data-usage]'))).toBe('needs login');
  });

  it('keeps the usage line when the accounts topic sends a row without those fields', async () => {
    const s = await mount();
    await until(() => text(card('beta').querySelector('[data-usage]')).includes('cannot'), 'the usage line');
    await act(async () => void s.daemon.accounts.set({ account: 'beta', cap: 3 }));
    await until(() => text(card('beta').querySelector('.acct-agents')).includes('/3'), 'the event');
    expect(text(card('beta').querySelector('[data-usage]'))).toContain('cannot take another agent: 5h 88% at last reading');
  });

  it('the gauge changes colour at 60 and 85 percent and always says the number', async () => {
    const s = setup();
    for (const p of [59, 60, 84, 85])
      s.daemon.accounts.add({ name: `p${p}`, five_hour: { used_pct: p, resets_at: null, source: 'measured' } });
    await mount('#/accounts', s);
    await until(() => $$('.acct-row').length === 7, 'the rows');
    const tone = (n: string) => card(n).querySelector('[data-gauge="5 h"]')!.getAttribute('data-tone');
    expect([59, 60, 84, 85].map((p) => tone(`p${p}`))).toEqual(['ok', 'warn', 'warn', 'bad']);
    expect(text(card('p85').querySelector('.gauge-pct'))).toBe('85%');
  });

  it('from 80 percent a window has a warning glyph and a full one its own, so the hue is not the only sign', async () => {
    const s = setup();
    for (const p of [79, 80, 100])
      s.daemon.accounts.add({ name: `g${p}`, five_hour: { used_pct: p, resets_at: null, source: 'measured' } });
    await mount('#/accounts', s);
    await until(() => card('g100'), 'the rows');
    const glyph = (n: string) => card(n).querySelector('[data-gauge="5 h"] .gauge-glyph svg')?.getAttribute('data-icon') ?? null;
    expect([79, 80, 100].map((p) => glyph(`g${p}`))).toEqual([null, 'warn', 'full']);
    const shape = (n: string) => card(n).querySelector('[data-gauge="5 h"] .gauge-glyph svg')?.innerHTML;
    expect(shape('g100')).not.toBe(shape('g80'));
  });

  it('an account whose window is full is out until the reset', async () => {
    const s = setup();
    s.daemon.accounts.add({
      name: 'full',
      five_hour: { used_pct: 100, resets_at: new Date(Date.now() + 3_600_000).toISOString(), source: 'measured' },
    });
    await mount('#/accounts', s);
    await until(() => card('full'), 'the card');
    expect(card('full').querySelector('.acct-state')!.getAttribute('data-state')).toBe('out');
    expect(text(card('full').querySelector('.acct-state'))).toContain('out until');
  });

  it('a daemon that does not serve accounts says so and has no popup', async () => {
    await mount('#/accounts', setup({ daemon: { accounts: false } }), '1');
    await until(() => text($('.accounts')).includes('does not serve accounts'), 'the notice');
    expect($('.usage')).toBeNull();
  });
});

describe('the usage popup', () => {
  it('the status bar item opens a line per account with gauges, agents and runway, linking to its page', async () => {
    await mount('#/agents/handline.architect', setup(), '1');
    await until(() => $('.usage-btn') && ($('.usage-btn')?.getAttribute('title') ?? '').includes('alpha 41%'), 'the item');
    expect($('.usage-btn')?.getAttribute('title') ?? '').toBe('Account usage (5 h): alpha 41% · review 88% · gamma –');
    await click($('.usage-btn'));
    const rows = $$('.usage-row');
    expect(rows).toHaveLength(3);
    expect(text(rows[0])).toContain('alpha');
    expect(text(rows[0])).toContain('agents 1 / cap 4');
    expect(text(rows[1])).toContain('runway');
    expect(rows[1]!.getAttribute('href')).toBe('#/accounts/beta');
    await click($('.usage-all'));
    expect(location.hash).toBe('#/accounts');
    expect($('.usage-pop')).toBeNull();
  });

  it('follows the accounts topic', async () => {
    const s = await mount('#/agents/handline.architect', setup(), '1');
    await until(() => ($('.usage-btn')?.getAttribute('title') ?? '').includes('alpha 41%'), 'the item');
    await act(async () => void s.daemon.accounts.set({ account: 'alpha', label: 'main' }));
    await until(() => ($('.usage-btn')?.getAttribute('title') ?? '').includes('main 41%'), 'the new alias');
  });
});

describe('one account', () => {
  it('opens from the list with Overview, Agents and Settings', async () => {
    await mount();
    await until(() => $$('.acct-row').length === 3, 'the rows');
    await click(card('alpha'));
    await act(async () => void dispatchEvent(new Event('hashchange'))); // happy-dom moves the hash without the event
    await until(() => $('.acct-one'), 'the account');
    expect(text($('.acct-overview'))).toContain('lasts');
    expect(text($('.acct-overview'))).toContain('renewing');
    await click(button('Agents'));
    expect(text($('.acct-agents-list'))).toContain('handline.architect');
    await click(button('Settings'));
    expect($('.acct-settings')).not.toBeNull();
  });

  it('a gone account says so with a link back', async () => {
    await mount('#/accounts/nope');
    await until(() => text($('.accounts')).includes('This account no longer exists'), 'the notice');
    expect($('.accounts a[href="#/accounts"]')).not.toBeNull();
  });

  it('the alias, cap, recovery and month settings save through the daemon', async () => {
    const s = await mount('#/accounts/alpha');
    await until(() => $('.acct-one'), 'the account');
    await click(button('Settings'));
    const [alias, cap, last] = $$('.acct-settings input:not([type="checkbox"])') as HTMLInputElement[];
    await setValue(alias!, 'main');
    await blur(alias!);
    await until(() => s.daemon.accounts.list().accounts[0]!.label === 'main', 'the alias');
    await setValue(cap!, '6');
    await blur(cap!);
    await until(() => s.daemon.accounts.list().accounts[0]!.cap === 6, 'the cap');
    const [recover, cycle] = $$('.acct-settings [role="switch"]');
    await click(recover);
    await until(() => s.daemon.accounts.list().accounts[0]!.recover, 'recovery');
    await click(cycle);
    await until(() => s.daemon.accounts.list().accounts[0]!.cycle.on, 'the cycle');
    await setValue(last!, '2026-11-02');
    await blur(last!);
    await until(() => s.daemon.accounts.list().accounts[0]!.cycle.end === '2026-11-02', 'the last day');
    expect(s.daemon.accounts.list().accounts[0]!.cycle.end_is_default).toBe(false);
  });

  it("a refused save puts the value back and shows the daemon's reason", async () => {
    const s = await mount('#/accounts/alpha');
    await until(() => $('.acct-one'), 'the account');
    await click(button('Settings'));
    s.daemon.knobs.refuse('account.set', 'role', 'only the founder may change an account');
    const [recover] = $$('.acct-settings [role="switch"]');
    await click(recover);
    await until(() => $('.toast'), 'the toast');
    expect(text($('.toast'))).toContain('only the founder');
    expect($$('.acct-settings [role="switch"]')[0]!.getAttribute('aria-checked')).toBe('false');
  });

  describe('login by url and code', () => {
    const codeBox = () => $('.acct-login input')! as HTMLInputElement;
    const started = async (s = setup()) => {
      await mount('#/accounts/gamma', s);
      await until(() => $('.acct-one'), 'the account');
      await click(button('Log in'));
      await until(() => $('.acct-login-url'), 'the url');
      return s;
    };

    it('Log in shows the URL to open and copy with a code box, Submit and Cancel, and asks for no credential', async () => {
      await started();
      expect($('.acct-login-url')!.getAttribute('href')).toMatch(/^https:/);
      expect(button('Copy')).toBeTruthy();
      expect(codeBox()).toBeTruthy();
      expect((button('Submit') as HTMLButtonElement)!.disabled).toBe(true); // nothing typed yet
      expect(button('Cancel')).toBeTruthy();
      expect($('input[type="password"], input[name*="token" i], input[name*="password" i]')).toBeNull();
      expect(text($('.acct-login'))).toContain('Log in from a computer.'); // the phone line stays
    });

    it('the code goes through account.login_code once, a right one ends the login, and the box is gone with it', async () => {
      const s = setup();
      const calls: string[] = [];
      const real = s.client.call.bind(s.client);
      // @ts-expect-error a test spy on the one client
      s.client.call = (m: string, p: never) => (calls.push(m), real(m, p));
      await started(s);
      await setValue(codeBox(), 'good-code'); // the fake daemon's right code
      await click(button('Submit'));
      await until(() => s.daemon.accounts.list().accounts.find((a) => a.name === 'gamma')?.login_state === 'ok', 'the login to finish');
      expect(calls.filter((m) => m === 'account.login_code')).toHaveLength(1);
      expect(button('Log in again')).toBeTruthy();
      expect($('.acct-login input')).toBeNull();
    });

    it('a wrong code ends the login failed and the page says why', async () => {
      const s = await started();
      await setValue(codeBox(), 'nope');
      await click(button('Submit'));
      await until(() => text($('.acct-login')).includes('the code was not accepted'), 'the reason');
      expect(s.daemon.accounts.list().accounts.find((a) => a.name === 'gamma')?.login_state).toBe('needs_login');
    });

    it('a refusal with gate state keeps the box and says why', async () => {
      const s = await started();
      s.daemon.knobs.refuse('account.login_code', 'state', 'the login is not waiting for a code');
      await setValue(codeBox(), 'good-code');
      await click(button('Submit'));
      await until(() => text($('.acct-login')).includes('not waiting'), 'the refusal');
      expect(codeBox()).toBeTruthy();
    });

    it('Cancel sends account.login_cancel and the account can log in again', async () => {
      const s = await started();
      await click(button('Cancel'));
      await until(() => s.daemon.accounts.list().accounts.find((a) => a.name === 'gamma')?.login?.state === 'cancelled', 'the cancel');
      expect((button('Log in') as HTMLButtonElement)!.disabled).toBe(false);
    });

    it('a login another client started shows no URL, offers Cancel, and then Log in again', async () => {
      const s = await mount('#/accounts/gamma');
      await until(() => $('.acct-one'), 'the account');
      // The host CLI starts the login from its own connection while the page watches: the topic tells it, without a URL of its own.
      const host = second(s.daemon, s.net);
      await host.start();
      await host.call('account.login', { account: 'gamma', request_id: 'l0' });
      await until(() => text($('.acct-login')).includes('Login in progress'), 'the running login');
      expect($('.acct-login-url')).toBeNull();
      expect(button('Log in')).toBeUndefined();
      await click(button('Cancel'));
      await until(() => (button('Log in') as HTMLButtonElement) && !(button('Log in') as HTMLButtonElement)!.disabled, 'the button back');
      expect(s.daemon.accounts.list().accounts.find((a) => a.name === 'gamma')?.login?.state).toBe('cancelled');
    });
  });
  describe('remove', () => {
    const open = async (name: string, s = setup()) => {
      await mount(`#/accounts/${name}`, s);
      await until(() => $('.acct-one'), 'the account');
      await click(button('Settings'));
      await click(button('Remove account'));
      await until(() => $('.ui-backdrop .acct-plan, .ui-backdrop .ui-note'), 'the dry run');
      return s;
    };
    const confirm = () => $$('.ui-backdrop button').find((b) => text(b).startsWith('Remove account')) as HTMLButtonElement;

    it('an account with agents on it shows them and the refusal, and cannot be confirmed', async () => {
      const s = await open('alpha');
      expect(text($('.ui-backdrop'))).toContain('architect');
      expect($('.ui-backdrop input')).toBeNull();
      expect(confirm().disabled).toBe(true);
      expect(s.daemon.accounts.list().accounts.map((a) => a.name)).toContain('alpha');
    });

    it('an empty account is removed only once its name is typed, and the page then says it is gone', async () => {
      const s = await open('gamma');
      expect(text($('.acct-plan'))).toContain('take gamma off the roster');
      expect(confirm().disabled).toBe(true);
      await setValue($('.ui-backdrop input') as HTMLInputElement, 'gam');
      expect(confirm().disabled).toBe(true);
      await setValue($('.ui-backdrop input') as HTMLInputElement, 'gamma');
      await click(confirm());
      await until(() => text($('.acct-one, .accounts')).includes('no longer exists'), 'the page to say it is gone');
      expect(s.daemon.accounts.list().accounts.map((a) => a.name)).toEqual(['alpha', 'beta']);
    });

    it('an agent landing on the account after the dry run refuses the removal: the dialog says why and the account stays', async () => {
      const s = await open('gamma');
      await setValue($('.ui-backdrop input') as HTMLInputElement, 'gamma');
      s.daemon.knobs.refuse('account.remove', 'state', 'agent_on_account', 'gamma has the auditor on it');
      await click(confirm());
      await until(() => $('.ui-backdrop [role="alert"]'), 'the refusal');
      expect(text($('.ui-backdrop [role="alert"]'))).toContain('gamma has the auditor on it');
      expect(confirm().disabled).toBe(false);
      expect(text(confirm())).not.toContain('Removing');
      expect(s.daemon.accounts.list().accounts.map((a) => a.name)).toContain('gamma');
    });

    it('is not offered to anyone but the founder', async () => {
      await mount('#/accounts/gamma', setup({ role: 'agent' }));
      await until(() => $('.acct-one'), 'the account');
      await click(button('Settings'));
      expect(button('Remove account')).toBeUndefined();
    });
  });
});
