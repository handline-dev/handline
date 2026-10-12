// The phone's Accounts tab on the fake daemon: the list with gauges, one account, alias and cap saving and a refused save, and remove with
// its dry run, typed confirmation, refusal and founder-only gate.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { DataProvider } from '@handline/app-data';
import { DaemonProvider } from '@handline/app-react';
import { setup } from '@handline/fake-daemon/helpers';
import { Home } from '../src/screens/Home.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const sleep = (ms: number) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, ms = 4000) {
  for (let t = 0; t < ms; t += 10) {
    if (cond()) return;
    await sleep(10);
  }
  throw new Error(`timed out waiting for ${what}`);
}

let root: Root | null = null;
let box: HTMLElement;
const $ = (id: string) => box.querySelector<HTMLElement>(`[data-testid="${id}"]`);
const text = (el: Element | null = box) => el?.textContent ?? '';
const press = (el: HTMLElement | null) => act(async () => void el?.click());
const type = (id: string, value: string) =>
  act(async () => {
    const el = $(id) as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

async function mount(role?: 'founder' | 'agent') {
  const s = setup({ role });
  const store = new Store(s.client);
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(
      <DaemonProvider store={store}>
        <DataProvider daemonId="test">
          <Home store={store} onUnpair={() => {}} />
        </DataProvider>
      </DaemonProvider>,
    );
  });
  await press($('nav-accounts'));
  return { ...s, store };
}
const openOne = async (name: string) => {
  await until(() => $(`account-${name}`), 'the account row');
  await press($(`account-${name}`));
  await until(() => $('account-detail'), 'the account');
};

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('the accounts tab', () => {
  it('lists each account with its state and gauges, and opens one with Back to the list', async () => {
    const m = await mount();
    await until(() => $('account-alpha'), 'the rows');
    expect(text($('account-alpha'))).toContain('41%');
    expect(text($('account-beta'))).toContain('review');
    expect(text($('account-gamma'))).toContain('login needed');
    await openOne('alpha');
    expect(text($('account-detail'))).toContain('On it:');
    await press($('account-back'));
    await until(() => $('account-beta'), 'the list');
    m.store.stop();
  });

  it('saves the alias and cap through the daemon, and a refused save says why', async () => {
    const m = await mount();
    await openOne('alpha');
    await type('account-alias', 'main');
    await type('account-cap', '2');
    await press($('account-save'));
    await until(() => m.daemon.accounts.list().accounts[0]!.label === 'main', 'the save');
    expect(m.daemon.accounts.list().accounts[0]!.cap).toBe(2);
    m.daemon.knobs.refuse('account.set', 'role', 'only the founder may change an account');
    await type('account-alias', 'other');
    await press($('account-save'));
    await until(() => text().includes('only the founder'), 'the refusal');
    m.store.stop();
  });

  it('removes an empty account only once its name is typed', async () => {
    const m = await mount();
    await openOne('gamma');
    await press($('account-remove-open'));
    await until(() => $('plan-line'), 'the dry run');
    await press($('sheet-submit')); // the name is not typed yet: nothing is sent
    expect(m.daemon.accounts.list().accounts.map((a) => a.name)).toContain('gamma');
    await type('sheet-confirm', 'gamma');
    await press($('sheet-submit'));
    await until(() => $('empty'), 'the page to say it is gone');
    expect(m.daemon.accounts.list().accounts.map((a) => a.name)).toEqual(['alpha', 'beta']);
    m.store.stop();
  });

  it('shows the agents on an account and offers no confirmation', async () => {
    const m = await mount();
    await openOne('alpha');
    await press($('account-remove-open'));
    await until(() => $('plan-line'), 'the dry run');
    expect(text($('sheet'))).toContain('Move them off first');
    expect($('sheet-confirm')).toBeNull();
    m.store.stop();
  });

  it('a refused removal says why and keeps the account', async () => {
    const m = await mount();
    await openOne('gamma');
    await press($('account-remove-open'));
    await until(() => $('sheet-confirm'), 'the confirmation');
    await type('sheet-confirm', 'gamma');
    m.daemon.knobs.refuse('account.remove', 'state', 'agent_on_account', 'gamma has the auditor on it');
    await press($('sheet-submit'));
    await until(() => $('sheet-error'), 'the refusal');
    expect(text($('sheet-error'))).toContain('gamma has the auditor on it');
    expect(text($('sheet-submit'))).not.toContain('Removing');
    expect(m.daemon.accounts.list().accounts.map((a) => a.name)).toContain('gamma');
    m.store.stop();
  });

  it('offers Remove to the founder only', async () => {
    const m = await mount('agent');
    await openOne('gamma');
    expect($('account-remove')).toBeNull();
    m.store.stop();
  });

  it('adds an account by name, and a used name is refused with the reason', async () => {
    const m = await mount();
    await press($('account-add-open'));
    await press($('sheet-submit')); // no name yet: nothing is sent
    expect(m.daemon.accounts.list().accounts.map((a) => a.name)).toEqual(['alpha', 'beta', 'gamma']);
    await type('sheet-name', 'alpha');
    await press($('sheet-submit'));
    await until(() => $('sheet-error'), 'the refusal');
    expect(text($('sheet-error'))).toContain('exists');
    await type('sheet-name', 'delta');
    await press($('sheet-submit'));
    await until(() => $('account-delta'), 'the new row');
    expect($('sheet')).toBeNull();
    m.store.stop();
  });

  it('offers Add account to the founder only', async () => {
    const m = await mount('agent');
    expect($('account-add-open')).toBeNull();
    m.store.stop();
  });
});
