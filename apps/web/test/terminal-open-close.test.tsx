// Close terminal and Open terminal above the terminal tab, against the fake daemon.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect'; // idle with a session and a terminal
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV => {
  const data = new Map<string, string>();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const button = (name: string) => [...box.querySelectorAll<HTMLButtonElement>('.terminal-bar button')].find((b) => b.textContent === name);
const click = (el: Element) => act(async () => void (el as HTMLElement).click());

async function mount(s = setup()) {
  const store = new Store(s.client);
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
  await until(() => box.querySelector('[data-tab^="terminal"]'), 'the terminal tab');
  await act(async () => void box.querySelector<HTMLElement>('[data-tab^="terminal"]')!.click());
  await until(() => button('Close terminal'), 'the terminal bar');
  return s;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('Close terminal', () => {
  it('is held back while a session runs, and says to stop first', async () => {
    await mount();
    expect(button('Close terminal')!.disabled).toBe(true);
    expect(box.querySelector('.terminal-bar')!.textContent).toContain('Stop the session first');
  });

  it('closes the terminal with one call per click once the session is stopped', async () => {
    const s = await mount();
    await s.client.call('session.stop', { agent: ARCH, mode: 'close', request_id: 'stop-1' } as never);
    await until(() => !button('Close terminal')!.disabled, 'Close terminal to enable');
    const call = vi.spyOn(s.client, 'call');
    await click(button('Close terminal')!);
    await click(button('Close terminal') ?? box);
    await until(() => !button('Close terminal'), 'the bar to go');
    expect(call.mock.calls.filter(([m]) => m === 'terminal.close')).toHaveLength(1);
    expect(box.textContent).toContain('No terminal');
  });
});

describe('Open terminal', () => {
  it('shows the host and the attach command, and Copy puts it on the clipboard', async () => {
    await mount();
    const write = vi.fn();
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true });
    await click(button('Open terminal')!);
    await until(() => box.querySelector('.terminal-bar code'), 'the attach command');
    expect(box.querySelector('.terminal-bar')!.textContent).toContain('Terminal on fake');
    await click(button('Copy')!);
    expect(write).toHaveBeenCalledWith(`fake-attach term:${ARCH}`);
  });
});
