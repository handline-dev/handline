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
  it('names both builds in the top bar when they differ, and Restart daemon asks the app to move the daemon', async () => {
    const restart = vi.fn(async () => ({ ok: true }));
    await mount({ sha: 'abc123def456', canRestart: true, restart });
    await until(() => $('.topbar .daemon-build'), 'the notice');
    expect($('.topbar .daemon-build')!.textContent).toContain('Daemon is fake000, app is abc123d');
    await act(async () => void ($('.topbar .daemon-build button') as HTMLElement).click());
    expect(restart).toHaveBeenCalledOnce();
  });
  it('says why when the daemon will not move', async () => {
    const refuse = async () => ({ ok: false, code: 'cannot_restart', message: 'exe prints another sha' });
    await mount({ sha: 'abc123def456', canRestart: true, restart: refuse });
    await until(() => $('.topbar .daemon-build button'), 'the button');
    await act(async () => void ($('.topbar .daemon-build button') as HTMLElement).click());
    await until(() => $('.topbar .daemon-build')!.textContent!.includes('Not restarted: exe prints another sha'), 'the reason');
  });
  it("shows the daemon's whole refusal, wrapped and not cut", async () => {
    const why = 'the daemon did not upgrade: daemon.upgrade is for a founder or above; this caller is anonymous';
    await mount({ sha: 'abc123def456', canRestart: true, restart: async () => ({ ok: false, code: 'cannot_restart', message: why }) });
    await until(() => $('.topbar .daemon-build button'), 'the button');
    await act(async () => void ($('.topbar .daemon-build button') as HTMLElement).click());
    await until(() => $('.topbar .daemon-build [role=alert]'), 'the reason');
    const alert = $('.topbar .daemon-build [role=alert]')!;
    expect(alert.textContent).toBe(`Not restarted: ${why}`);
    expect(alert.className).not.toContain('truncate');
  });
  it('says nothing for the same build or in a plain browser, and offers no button for a daemon on another device', async () => {
    await mount({ sha: 'fake0000', canRestart: true });
    expect($('.topbar .daemon-build')).toBeNull();
    await unmount();
    await mount(null);
    expect($('.topbar .daemon-build')).toBeNull();
    await unmount();
    await mount({ sha: 'abc123def456', canRestart: false });
    await until(() => $('.topbar .daemon-build'), 'the notice');
    expect($('.topbar .daemon-build button')).toBeNull();
  });
});
