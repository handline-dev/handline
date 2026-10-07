// U14: the Accounts page and the usage popup on the fake daemon's accounts: skeleton, cards with gauges and state chips, the popup, one account's
// tabs, settings that save and revert, login that opens no credential field, and a daemon that does not serve accounts.
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
  it('shows the skeleton while loading, then a card per account with its gauges, state chip and agents', async () => {
    const s = setup();
    s.daemon.knobs.latency(400);
    await mount('#/accounts', s);
    await until(() => $('.accounts .skeleton'), 'the skeleton');
    expect($('.accounts .skeleton')!.getAttribute('aria-busy')).toBe('true');
    await until(() => $$('.acct-card').length === 3, 'the cards', 600);
    expect($('.accounts .skeleton')).toBeNull();
    expect(text(card('alpha').querySelector('.acct-state'))).toBe('in use');
    expect(text(card('beta').querySelector('strong'))).toBe('review');
    expect(text(card('gamma').querySelector('.acct-state'))).toBe('login needed');
    expect(text(card('alpha').querySelector('[data-gauge="5 h"] .gauge-pct'))).toBe('41%');
    expect(text(card('alpha').querySelector('[data-gauge="5 h"] .gauge-reset'))).toMatch(/^resets in 2 h/);
    expect(text(card('alpha'))).toContain('agents 1 / cap 4');
    expect(text(card('gamma'))).toContain('no reading');
  });

  it('the gauge changes colour at 60 and 85 percent and always says the number', async () => {
    const s = setup();
    for (const p of [59, 60, 84, 85])
      s.daemon.accounts.add({ name: `p${p}`, five_hour: { used_pct: p, resets_at: null, source: 'measured' } });
    await mount('#/accounts', s);
    await until(() => $$('.acct-card').length === 7, 'the cards');
    const tone = (n: string) => card(n).querySelector('[data-gauge="5 h"]')!.getAttribute('data-tone');
    expect([59, 60, 84, 85].map((p) => tone(`p${p}`))).toEqual(['ok', 'warn', 'warn', 'bad']);
    expect(text(card('p85').querySelector('.gauge-pct'))).toBe('85%');
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
    await until(() => $('.usage-btn') && text($('.usage-btn')).includes('alpha 41%'), 'the item');
    expect(text($('.usage-btn'))).toBe('acct alpha 41% · review 88% · gamma –');
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
    await until(() => text($('.usage-btn')).includes('alpha 41%'), 'the item');
    await act(async () => void s.daemon.accounts.set({ account: 'alpha', label: 'main' }));
    await until(() => text($('.usage-btn')).includes('main 41%'), 'the new alias');
  });
});

describe('one account', () => {
  it('opens from the list with Overview, Agents and Settings', async () => {
    await mount();
    await until(() => $$('.acct-card').length === 3, 'the cards');
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
    const [recover, cycle] = $$('.acct-settings input[type="checkbox"]') as HTMLInputElement[];
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
    const [recover] = $$('.acct-settings input[type="checkbox"]') as HTMLInputElement[];
    await click(recover);
    await until(() => $('.toast'), 'the toast');
    expect(text($('.toast'))).toContain('only the founder');
    expect(($$('.acct-settings input[type="checkbox"]')[0] as HTMLInputElement).checked).toBe(false);
  });

  it('login runs in a terminal the daemon opens: the page asks for no credential, and the progress ends when the topic says ok', async () => {
    const s = await mount('#/accounts/gamma');
    await until(() => $('.acct-one'), 'the account');
    expect($('input[type="password"], input[name*="token" i], input[name*="password" i]')).toBeNull();
    await click(button('Log in'));
    await until(() => text($('.acct-login')).includes('Login in progress in terminal term:login:gamma'), 'the progress');
    expect(text($('.acct-login'))).toContain('hl login gamma');
    expect(($$('button').find((b) => text(b) === 'Log in') as HTMLButtonElement).disabled).toBe(true);
    await act(async () => void s.daemon.accounts.loggedIn('gamma'));
    await until(() => !text($('.acct-login')).includes('Login in progress'), 'the progress to end');
  });
});
