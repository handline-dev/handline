// Logging an account in from the phone on the fake daemon: the URL as text, the code once, a wrong code, Cancel, and the founder-only gate.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
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

const started = async () => {
  await openOne('gamma');
  await press($('login-open'));
  await until(() => $('login-url'), 'the url');
};

describe('account login', () => {
  it('shows the URL as text with a code box, Submit and Cancel, and asks for no credential', async () => {
    const m = await mount();
    await started();
    expect(text($('login-url'))).toMatch(/^https:/);
    expect($('login-code')).toBeTruthy();
    expect(($('login-submit') as HTMLButtonElement).disabled).toBe(true);
    expect($('login-cancel')).toBeTruthy();
    m.store.stop();
  });

  it('sends the code once, and a right one ends the login', async () => {
    const m = await mount();
    const spy = vi.spyOn(m.client, 'call');
    await started();
    await type('login-code', 'good-code');
    await press($('login-submit'));
    await until(() => m.daemon.accounts.list().accounts.find((a) => a.name === 'gamma')?.login_state === 'ok', 'the login to finish');
    expect(spy.mock.calls.filter(([n]) => n === 'account.login_code')).toHaveLength(1);
    await until(() => text($('login-open')).includes('Log in again'), 'the control back');
    expect($('login-code')).toBeNull();
    m.store.stop();
  });

  it('a wrong code keeps the account unauthenticated and says why', async () => {
    const m = await mount();
    await started();
    await type('login-code', 'nope');
    await press($('login-submit'));
    await until(() => text($('account-login')).includes('the code was not accepted'), 'the reason');
    expect(m.daemon.accounts.list().accounts.find((a) => a.name === 'gamma')?.login_state).toBe('needs_login');
    m.store.stop();
  });

  it('a refused code keeps the box and says why', async () => {
    const m = await mount();
    await started();
    m.daemon.knobs.refuse('account.login_code', 'state', 'the login is not waiting for a code');
    await type('login-code', 'good-code');
    await press($('login-submit'));
    await until(() => $('login-error'), 'the refusal');
    expect(text($('login-error'))).toContain('not waiting');
    expect($('login-code')).toBeTruthy();
    m.store.stop();
  });

  it('Cancel sends account.login_cancel and the account can log in again', async () => {
    const m = await mount();
    await started();
    await press($('login-cancel'));
    await until(() => m.daemon.accounts.list().accounts.find((a) => a.name === 'gamma')?.login?.state === 'cancelled', 'the cancel');
    await until(() => $('login-open'), 'Log in back');
    m.store.stop();
  });

  it('shows no control to a member phone', async () => {
    const m = await mount('agent');
    await openOne('gamma');
    expect($('account-login')).toBeNull();
    m.store.stop();
  });
});
