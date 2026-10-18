// The working indicator follows the agent being busy, whoever wrote the message it is busy with.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
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

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('the working indicator', () => {
  it('shows while the agent is busy, also on a message from another agent', async () => {
    const s = setup();
    location.hash = `#/agents/${ARCH}`;
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
    });
    await until(() => box.querySelector('textarea.compose'), 'the chat');
    expect(box.querySelector('.working')).toBeNull();
    s.daemon.chat.seed(ARCH, { from: 'handline.auditor', to: ARCH, body: 'rebase the pane branch' });
    s.daemon.chat.setWorking(ARCH, true, 'Finding large disk users');
    await until(() => box.querySelector('.working')?.getAttribute('title') === 'Finding large disk users', 'the indicator');
    s.daemon.chat.setWorking(ARCH, false);
    await until(() => box.querySelector('.working') === null, 'the indicator gone');
  });
});
