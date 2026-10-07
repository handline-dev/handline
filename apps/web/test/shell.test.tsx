// The desktop shell's calls from the page (docs/electron-bridge.md): `screen`, `unsent`, `onNavigate`, and a plain browser with none.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV, type NavigateTo, type ShellApi } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';
import { shell } from '../src/shell.ts';

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
  location.hash = `#/agents/${agent}`;
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
    const s = await mount('handline.term-build');
    await until(() => sh.log.unsent.length > 0, 'the first value');
    const start = () => [...box.querySelectorAll('button')].find((b) => /Start$/.test(b.textContent ?? ''));
    await until(start, 'Start');
    s.daemon.knobs.latency(400);
    await act(async () => void start()!.click());
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

  it('a finished task opens its unread inbox card, and the task itself once none is left', async () => {
    const sh = stubShell();
    await mount(ARCH);
    await until(() => sh.hasListener() && document.title.startsWith('(3)'), 'the listener and the inbox');
    await sh.navigate({ screen: 'task', id: '12', event: 'task_done' }); // unread result of task 12
    expect(location.hash).toBe('#/inbox/5');
    await sh.navigate({ screen: 'task', id: '7', event: 'task_done' }); // its result was read
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
