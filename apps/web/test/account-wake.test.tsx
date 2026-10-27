// A stalled account: the Usage list and the account's page show blocked and its reason; the founder's Wake ends it, a refusal says why,
// and a member or a daemon without account.wake shows no Wake.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { applySeeds } from '@handline/fake-daemon/seeds';
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
const wakeButton = () => [...box.querySelectorAll<HTMLElement>('[data-stall] button')].find((b) => b.textContent?.startsWith('Wak'));

async function mount(hash: string, s = setup()) {
  applySeeds(s.daemon, 'account-stalled');
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => $('[data-account="beta"]') || $('.acct-one'), 'the accounts');
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('a stalled account', () => {
  it('shows blocked and its reason in the list, only on the stalled account', async () => {
    await mount('#/accounts');
    await until(() => $('[data-stall="beta"]'), 'the stall');
    expect($('[data-stall="beta"]')!.textContent).toContain('blocked');
    expect($('[data-stall="beta"]')!.textContent).toContain('billing_error');
    expect($('[data-stall="alpha"]')).toBeNull();
  });

  it('Wake ends the stall, on the list and on the account page', async () => {
    const s = await mount('#/accounts');
    await until(wakeButton, 'Wake');
    await act(async () => void wakeButton()!.click());
    await until(() => !$('[data-stall="beta"]'), 'the stall to end');
    expect(s.daemon.accounts.list().accounts.find((a) => a.name === 'beta')!.stalled).toBeNull();
  });

  it('shows the stall on the account page', async () => {
    await mount('#/accounts/beta');
    await until(() => $('.acct-one [data-stall="beta"]'), 'the stall');
    await until(wakeButton, 'Wake');
  });

  it('says why when the daemon refuses, and puts the stall back', async () => {
    const s = await mount('#/accounts');
    await until(wakeButton, 'Wake');
    // The daemon's stall ended behind this page's back (no topic event yet), so its wake is refused.
    (s.daemon.accounts as unknown as { rows: { name: string; stalled: unknown }[] }).rows.find((r) => r.name === 'beta')!.stalled = null;
    await act(async () => void wakeButton()!.click());
    await until(() => $('[data-stall="beta"] [role="alert"]'), 'the refusal');
    expect($('[data-stall="beta"] [role="alert"]')!.textContent).toMatch(/not.stalled/);
  });

  it('shows no Wake to a daemon that does not serve account.wake', async () => {
    const s = setup();
    s.daemon.knobs.unserve?.('account.wake');
    await mount('#/accounts', s);
    await until(() => $('[data-stall="beta"]'), 'the stall');
    expect(wakeButton()).toBeUndefined();
  });
});
