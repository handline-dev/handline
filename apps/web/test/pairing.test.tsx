import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import type { KV } from '@handline/app-core';
import { FakeDaemon } from '@handline/fake-daemon/daemon';
import { memoryPair } from '@handline/client';
import { Start } from '../src/Start.tsx';
import { codeFrom, needsToken } from '../src/pairing.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) =>
  act(async () => {
    await new Promise((r) => setTimeout(r, ms));
  });
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV & { data: Map<string, string> } => {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  root = null;
});

describe('pairing with the network listener', () => {
  it('reads the code out of a pasted line and knows which urls want a token', () => {
    expect(codeFrom('  ABCD-1234 ')).toBe('ABCD-1234');
    expect(codeFrom('handline://pair?host=127.0.0.1:7443&code=ABCD-1234')).toBe('ABCD-1234');
    expect(needsToken('ws://127.0.0.1:7443/rpc')).toBe(true);
    expect(needsToken('ws://127.0.0.1:7777')).toBe(false);
  });

  it('asks for a code, keeps the token it gets, and shows the app', async () => {
    const daemon = new FakeDaemon();
    const url = 'ws://127.0.0.1:7443/rpc';
    const open = async () => {
      const [a, b] = memoryPair();
      daemon.acceptNetwork(b);
      return a;
    };
    const storage = memory();
    const box = document.createElement('div');
    document.body.append(box);
    root = createRoot(box);
    await act(async () => root!.render(<Start url={url} insecure={false} storage={storage} open={open} />));
    const input = box.querySelector<HTMLInputElement>('.pair-screen input')!;
    expect(input).not.toBeNull();

    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    await act(async () => {
      set.call(input, 'WRONG-CODE');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => box.querySelector<HTMLButtonElement>('.pair-screen button')!.click());
    await until(() => box.querySelector('[role="alert"]'), 'the refusal');
    expect(storage.data.size).toBe(0);

    await act(async () => {
      set.call(input, `handline://pair?host=x&code=${daemon.issueCode()}`);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => box.querySelector<HTMLButtonElement>('.pair-screen button')!.click());
    await until(() => !box.querySelector('.pair-screen'), 'the app');
    expect([...storage.data.values()][0]).toMatch(/^hld_/);
    await until(() => box.querySelector('#app'), 'the frame');
  });

  it('a stored token the listener no longer knows is dropped and the pairing screen says so', async () => {
    const daemon = new FakeDaemon(); // knows no token: the hello is refused with gate unpaired
    const url = 'ws://127.0.0.1:7443/rpc';
    const open = async () => {
      const [a, b] = memoryPair();
      daemon.acceptNetwork(b);
      return a;
    };
    const storage = memory();
    storage.setItem(`token:${url}`, 'hld_revoked');
    const box = document.createElement('div');
    document.body.append(box);
    root = createRoot(box);
    await act(async () => root!.render(<Start url={url} insecure={false} storage={storage} open={open} />));
    await until(() => box.querySelector('.pair-screen'), 'the pairing screen');
    expect(box.querySelector('[role="status"]')?.textContent).toBe('This computer is no longer paired.');
    expect(storage.getItem(`token:${url}`)).toBe('');
  });

  it('in the desktop app the pairing screen offers the other mode and pairing with another device by address and code', async () => {
    const calls: unknown[] = [];
    const reply = async (r: unknown) => (calls.push(r), { ok: true as const });
    (window as { handline?: unknown }).handline = {
      api: 1,
      mode: async () => ({ ok: true, mode: 'connect', remote: null }),
      setMode: (m: string) => reply(m),
      pairRemote: (a: unknown) => reply(a),
    };
    try {
      const box = document.createElement('div');
      document.body.append(box);
      root = createRoot(box);
      await act(async () => root!.render(<Start url="ws://127.0.0.1:7443/rpc" insecure={false} storage={memory()} />));
      await until(() => box.querySelector('.connection'), 'the connection section');
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      for (const [label, v] of [
        ['Address', 'mac.tail1.ts.net'],
        ['Device code', 'ABCD-1234'],
      ] as const)
        await act(async () => {
          const el = box.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
          set.call(el, v);
          el.dispatchEvent(new Event('input', { bubbles: true }));
        });
      const button = (t: string) => [...box.querySelectorAll<HTMLButtonElement>('.connection button')].find((b) => b.textContent === t)!;
      await act(async () => button('Pair with this device').click());
      await act(async () => button('Run its own daemon').click());
      expect(calls).toEqual([{ address: 'mac.tail1.ts.net', code: 'ABCD-1234' }, 'own']);
    } finally {
      delete (window as { handline?: unknown }).handline;
    }
  });
});
