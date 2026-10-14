// The # menu reads chat.refs: a request the loaded chat does not show is listed and picked; the agent's tasks stay.
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
const options = () => [...box.querySelectorAll<HTMLElement>('.compose-menu [role=option]')].map((o) => o.textContent ?? '');
const typeIntoCompose = (value: string) =>
  act(async () => {
    const el = box.querySelector<HTMLTextAreaElement>('textarea.compose')!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

async function mount(served = true) {
  const s = setup();
  // A request between two agents: the chat of the person (scope mine) does not show it, chat.refs lists it.
  const hidden = s.daemon.chat.seed(ARCH, { body: 'agent to agent: rebase the pane branch', from: 'handline.auditor', to: ARCH });
  if (!served) s.daemon.knobs.unserve('chat.refs');
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => box.querySelector('textarea.compose'), 'the message box');
  return hidden;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('# in the message box', () => {
  it('lists a request the loaded chat does not show', async () => {
    const hidden = await mount();
    await typeIntoCompose('#');
    await until(() => options().some((o) => o.includes('rebase the pane branch')), 'the request from chat.refs');
    expect(options().some((o) => o.includes(`#${hidden}`))).toBe(true);
  });

  it('shows no such request from a daemon that does not serve chat.refs', async () => {
    await mount(false);
    await typeIntoCompose('#');
    await wait(100);
    expect(options().some((o) => o.includes('rebase the pane branch'))).toBe(false);
  });
});
