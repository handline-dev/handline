// An agent with no terminal: nothing on its page says so, and Open terminal starts it and opens the terminal.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TB = 'handline.term-build';
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

let root: Root | null = null;
let box: HTMLElement;

async function mount(hash: string) {
  const s = setup();
  const sess = s.daemon.sessions.get(TB)!;
  sess.state = 'no_terminal';
  sess.hasTerminal = false;
  s.daemon.inbox.add({ kind: 'block', agent: TB, title: 'Recovery failed', body: 'x', fix: 'open_terminal' });
  location.hash = hash;
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
});

describe('an agent with no terminal', () => {
  it('shows no word for it over its chat, and no badge or tip says "no terminal"', async () => {
    await mount(`#/agents/${TB}`);
    await until(() => box.querySelector('h2'), 'the room title');
    await until(() => box.querySelector('[data-agent]'), 'the sidebar');
    expect(box.querySelector('h2')!.textContent!.trim()).toBe(TB);
    expect(box.innerHTML).not.toMatch(/no[_ ]terminal/i);
  });

  it('is started by Open terminal in My turn, and its terminal is the answer', async () => {
    const s = await mount('#/my-turn');
    await until(() => [...box.querySelectorAll('button')].some((b) => b.textContent === 'Open terminal'), 'the button');
    const b = [...box.querySelectorAll('button')].find((x) => x.textContent === 'Open terminal')!;
    expect(b.hasAttribute('disabled')).toBe(false);
    await act(async () => void (b as HTMLElement).click());
    await until(() => s.daemon.sessions.get(TB)!.hasTerminal, 'the agent started');
    await until(() => box.querySelector('.terminal-tab'), 'its terminal tab');
  });
});
