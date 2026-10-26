// The desktop shell's calls from the page (docs/electron-bridge.md): `screen`, `unsent`, `onNavigate`, and a plain browser with none.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV, type NavigateTo, type ShellApi } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';
import { settingsRelay, shell } from '#src/shell.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 300) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV => {
  const data = new Map<string, string>();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

/** A shell that records what the page told it; `only` leaves out the calls this shell does not have. */
function stubShell(only?: ('screen' | 'unsent' | 'onNavigate')[]) {
  const log = { screen: [] as (string | null)[], unsent: [] as boolean[] };
  let navigate: ((to: NavigateTo) => void) | null = null;
  const all = {
    api: 1,
    screen: async (s: { agent: string | null }) => (log.screen.push(s.agent), { ok: true as const }),
    unsent: async (on: boolean) => (log.unsent.push(on), { ok: true as const }),
    onNavigate: (cb: (to: NavigateTo) => void) => {
      navigate = cb;
      return () => void (navigate = null);
    },
  } satisfies ShellApi;
  const api = only ? Object.fromEntries(Object.entries(all).filter(([k]) => k === 'api' || only.includes(k as 'screen'))) : all;
  vi.stubGlobal('handline', api);
  Object.assign(window, { handline: api });
  return { log, navigate: (to: NavigateTo) => act(async () => navigate!(to)), hasListener: () => navigate !== null };
}

let root: Root | null = null;
let box: HTMLElement;
async function mount(agent: string) {
  const s = setup();
  location.hash = agent === 'settings' ? '#/settings' : `#/agents/${agent}`;
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
  vi.unstubAllGlobals();
  delete (window as { handline?: unknown }).handline;
});

describe('with the shell', () => {
  it('tells it which agent is on screen, and again when the agent changes', async () => {
    const sh = stubShell();
    await mount(ARCH);
    await until(() => sh.log.screen.includes(ARCH), 'screen called');
    await act(async () => {
      location.hash = '#/agents/handline.auditor';
      dispatchEvent(new Event('hashchange'));
    });
    await until(() => sh.log.screen.at(-1) === 'handline.auditor', 'the new agent');
    await act(async () => {
      location.hash = '';
      dispatchEvent(new Event('hashchange'));
    });
    await until(() => sh.log.screen.at(-1) === null, 'no agent');
  });

  it('says unsent while a field holds text and again, false, when it is empty; it says nothing while the value is the same', async () => {
    const sh = stubShell();
    await mount(ARCH);
    await until(() => sh.log.unsent.length > 0, 'the first value');
    expect(sh.log.unsent).toEqual([false]);
    const field = box.appendChild(document.createElement('textarea')); // any compose box: the check is on the page, not on one view
    await act(async () => {
      field.value = 'half a message';
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await until(() => sh.log.unsent.at(-1) === true, 'unsent');
    await act(async () => {
      field.value = '   ';
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await until(() => sh.log.unsent.at(-1) === false, 'not unsent');
    expect(sh.log.unsent).toEqual([false, true, false]);
  });

  it('says unsent while a write is in flight, and not after', async () => {
    const sh = stubShell();
    const s = await mount('handline.auditor');
    await until(() => sh.log.unsent.length > 0, 'the first value');
    const row = () => box.querySelector<HTMLElement>('[data-agent="handline.auditor"]')!;
    await until(row, 'the row');
    s.daemon.sessions.get('handline.auditor')!.state = 'running';
    await act(async () => s.daemon.chat.setTurn('handline.auditor', true));
    s.daemon.dropConnections(); // the rows are read again
    await until(() => row().querySelector('.row-main')?.getAttribute('title')?.includes('running'), 'the row running', 600);
    await act(async () => void row().dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true })));
    const item = [...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent === 'Interrupt') as HTMLElement;
    s.daemon.knobs.latency(400);
    await act(async () => void item.click());
    await until(() => sh.log.unsent.at(-1) === true, 'unsent while the write is in flight');
    await until(() => sh.log.unsent.at(-1) === false, 'not unsent after', 600);
  });

  it('a clicked notice goes to the route its target names; one with no route does nothing', async () => {
    const sh = stubShell();
    await mount(ARCH);
    await until(() => sh.hasListener(), 'the listener');
    await sh.navigate({ screen: 'agent', id: 'handline.auditor', event: 'stall' });
    expect(location.hash).toBe('#/agents/handline.auditor');
    await sh.navigate({ screen: 'task', id: 'None', event: 'task_done' });
    expect(location.hash).toBe('#/agents/handline.auditor');
  });

  it('a finished task opens the task', async () => {
    const sh = stubShell();
    await mount('handline.term-build');
    await until(() => sh.hasListener(), 'the listener');
    await sh.navigate({ screen: 'task', id: '7', event: 'task_done' });
    expect(location.hash).toBe('#/tasks/7');
  });

  it('uses only the calls this shell has', async () => {
    const sh = stubShell(['screen']);
    await mount(ARCH);
    await until(() => sh.log.screen.includes(ARCH), 'screen called');
    expect(sh.log.unsent).toEqual([]);
    expect(shell('unsent')).toBeNull();
    expect(shell('screen')).not.toBeNull();
  });
});

describe('Pair a phone', () => {
  const withMode = (phone: boolean, open: () => Promise<{ ok: true } | { ok: false; code: 'cannot_pair'; message: string }>) => {
    const api = {
      api: 1,
      onNavigate: () => () => {},
      mode: async () => ({ ok: true as const, mode: 'own' as const, remote: null, phone }),
      phone: open,
    };
    vi.stubGlobal('handline', api);
    Object.assign(window, { handline: api });
  };
  const button = () => [...box.querySelectorAll('button')].find((b) => b.textContent?.includes('Pair a phone')) as HTMLElement | undefined;

  it('is a button under Settings > Paired devices that opens the QR, and shows why when it cannot', async () => {
    let n = 0;
    withMode(true, async () => (n++ ? { ok: false, code: 'cannot_pair', message: 'no network listener' } : { ok: true }));
    await mount('settings');
    await until(button, 'the Pair a phone button');
    await act(async () => button()!.click());
    expect(n).toBe(1);
    expect(box.querySelector('[role=alert]')).toBeNull();
    await act(async () => button()!.click());
    await until(() => box.querySelector('[role=alert]')?.textContent === 'no network listener', 'the reason');
  });

  it('is absent when the app is paired with another device, and in a plain browser', async () => {
    withMode(false, async () => ({ ok: true }));
    await mount('settings');
    await until(() => box.querySelector('#set-devices .devices'), 'the devices');
    await wait(50);
    expect(button()).toBeUndefined();
  });
});

describe('Settings > Connection in the shell', () => {
  const text = () => box.querySelector('#set-connection, #set-devices')?.parentElement?.textContent ?? box.textContent ?? '';
  const named = (label: string) =>
    [...box.querySelectorAll('button')].find((b) => b.textContent?.includes(label)) as HTMLElement | undefined;
  const aboutFold = () =>
    [...box.querySelectorAll('button[aria-expanded]')].find((b) => b.textContent?.includes('About')) as HTMLElement | undefined;
  const stub = (follow: object) => {
    const log: string[] = [];
    const api = {
      api: 1,
      onNavigate: () => () => {},
      mode: async () => ({ ok: true as const, mode: 'own' as const, remote: null, phone: false }),
      setMode: async (m: string) => (log.push(`mode ${m}`), { ok: true as const }),
      follow: async () => ({ ok: true as const, ...follow }),
      checkUpdate: async () => (log.push('check'), { ok: true as const }),
    };
    vi.stubGlobal('handline', api);
    Object.assign(window, { handline: api });
    return log;
  };

  it('switches the mode and looks for an update from the page, in a follow-main build', async () => {
    const log = stub({ following: true, sha: 'abc1234567', state: 'current', next: null });
    await mount('settings');
    await until(() => aboutFold(), 'the About fold');
    await act(async () => aboutFold()!.click());
    await until(() => named('Switch to Connect'), 'the switch');
    await until(() => named('Check for update'), 'the update check');
    expect(text()).toContain('main abc1234');
    await act(async () => named('Check for update')!.click());
    await act(async () => named('Switch to Connect')!.click());
    expect(log).toEqual(['check', 'mode connect']);
  });

  it('offers no update check in a build that does not follow main, and none while a newer main is waiting', async () => {
    stub({ following: false, sha: '', state: 'current', next: null });
    await mount('settings');
    await until(() => aboutFold(), 'the About fold');
    await act(async () => aboutFold()!.click());
    await until(() => named('Switch to Connect'), 'the switch');
    await wait(50);
    expect(named('Check for update')).toBeUndefined();
  });
});

describe('settings through the shell', () => {
  it('answers settings.get and settings.set from the shell, and throws a refusal as a daemon refusal', async () => {
    expect(settingsRelay()).toBeUndefined(); // a plain browser
    const calls: unknown[] = [];
    const api = {
      api: 1,
      settingsGet: async (a: unknown) => (calls.push(a), { ok: true as const, result: { values: { active_cap: 2 } } }),
      settingsSet: async (a: unknown) => (calls.push(a), { ok: false as const, code: 'refused', message: 'no' }),
    };
    vi.stubGlobal('handline', api);
    Object.assign(window, { handline: api });
    const relay = settingsRelay()!;
    expect(await relay['settings.get']!({})).toEqual({ values: { active_cap: 2 } });
    await expect(relay['settings.set']!({ changes: { active_cap: 3 }, request_id: 'r' })).rejects.toMatchObject({
      code: 'refused',
      message: 'no',
    });
    expect(calls).toEqual([{}, { changes: { active_cap: 3 }, agent: undefined }]);
  });
});

describe('in a plain browser', () => {
  it('there is no shell, the page works, and nothing is called', async () => {
    expect((window as { handline?: unknown }).handline).toBeUndefined();
    expect(shell('screen')).toBeNull();
    await mount(ARCH);
    await until(() => box.querySelector('.rail-list'), 'the page');
  });

  it('an object that is not api 1 is not a shell', () => {
    Object.assign(window, { handline: { api: 2, screen: async () => ({ ok: true }) } });
    expect(shell('screen')).toBeNull();
  });
});
