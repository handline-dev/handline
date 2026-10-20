// A #N in a chat is the pull request of the chat agent's repo when it has one of that number, else the message link.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
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

let root: Root | null = null;
let box: HTMLElement;
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const msg = (body: string) => $$('.msg').find((m) => text(m).includes(body));

async function mount(agent: string, s = setup(), storage: KV = memory()) {
  const store = new Store(s.client);
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={storage} />);
  });
  return { store, storage, ...s };
}
const unmount = async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
};

afterEach(unmount);

describe('#N in a chat', () => {
  it("is the pull request of the agent's repo when it has that number, else plain text", async () => {
    const s = setup();
    s.daemon.pullRequests.add(306);
    s.daemon.chat.seed(ARCH, { from: ARCH, to: 'human', body: 'fixed in #306, the thread is #20', at: '2031-02-01T00:00:00Z' });
    await mount(ARCH, s);
    await until(() => msg('fixed in'), 'the message');
    const m = msg('fixed in')!;
    await until(() => m.querySelector('[data-pr]'), 'the pull request link');
    const pr = m.querySelector<HTMLAnchorElement>('[data-pr]')!;
    expect(pr.textContent).toBe('#306');
    expect(pr.href).toBe('https://github.com/handline-dev/handline/pull/306');
    expect([...m.querySelectorAll('button')].some((b) => b.textContent === '#20')).toBe(false);
    expect(text(m)).toContain('the thread is #20');
    expect(m.querySelectorAll('[data-pr]').length).toBe(1);
  });

  it('stays plain text when the daemon does not serve pr.exists', async () => {
    const s = setup();
    s.daemon.pullRequests.add(306);
    s.daemon.knobs.unserve('pr.exists');
    s.daemon.chat.seed(ARCH, { from: ARCH, to: 'human', body: 'fixed in #306', at: '2031-02-01T00:00:00Z' });
    await mount(ARCH, s);
    await until(() => msg('fixed in'), 'the message');
    await wait(50);
    expect(msg('fixed in')!.querySelector('[data-pr]')).toBeNull();
    expect(text(msg('fixed in'))).toContain('fixed in #306');
  });
});
