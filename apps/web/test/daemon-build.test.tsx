// The top bar's notice when the daemon is not the build of the app around it.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** The fake daemon says it is fake0000; the app around the page is `sha`. */
async function mount(shell: { sha: string | null; canRestart: boolean; restart?: () => Promise<unknown> } | null) {
  if (shell)
    vi.stubGlobal('handline', {
      api: 1,
      build: async () => ({ ok: true, sha: shell.sha, canRestart: shell.canRestart }),
      restartDaemon: shell.restart ?? (async () => ({ ok: true })),
    });
  const data = new Map<string, string>([['terminal', 'text']]);
  const storage: KV = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
  const store = new Store(setup().client);
  location.hash = '#/my-turn';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={storage} />);
  });
  await until(() => $('.topbar'), 'the top bar');
  await wait(50);
}
async function unmount() {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  vi.unstubAllGlobals();
}

beforeEach(() => history.replaceState(null, ''));
afterEach(unmount);

describe('the daemon build notice', () => {
  it('moves the daemon by itself once, and shows nothing when that works', async () => {
    const restart = vi.fn(async () => ({ ok: true }));
    await mount({ sha: 'abc123def456', canRestart: true, restart });
    await until(() => restart.mock.calls.length, 'the restart');
    await wait(50);
    expect(restart).toHaveBeenCalledOnce();
    expect($('.daemon-build')).toBeNull();
    expect(document.querySelector('.toast')).toBeNull();
  });
  it('does nothing for the same build, in a plain browser, for another device, or when a newer build waits on disk', async () => {
    const restart = vi.fn(async () => ({ ok: true }));
    await mount({ sha: 'fake0000', canRestart: true, restart });
    await unmount();
    await mount(null);
    await unmount();
    await mount({ sha: 'abc123def456', canRestart: false, restart });
    await wait(50);
    expect(restart).not.toHaveBeenCalled();
    expect(document.querySelector('.toast')).toBeNull();
  });
  it('shows a notice with Try again when the daemon will not move', async () => {
    const why = 'the daemon did not upgrade: daemon.upgrade is for a founder or above; this caller is anonymous';
    // A real refusal takes a round trip, which is after the store has finished starting (that clears the toasts).
    const restart = vi.fn(async () => (await wait(100), { ok: false, code: 'cannot_restart', message: why }));
    await mount({ sha: 'abc123def456', canRestart: true, restart });
    await until(() => document.body.textContent!.includes(why), 'the notice');
    const again = [...document.querySelectorAll('.toast button')].find((b) => b.textContent === 'Try again') as HTMLElement;
    await act(async () => void again.click());
    expect(restart).toHaveBeenCalledTimes(2);
    await wait(150);
  });
});
