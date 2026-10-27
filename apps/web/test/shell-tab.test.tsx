// Add a tab > Terminal: each one is its own shell in the agent's worktree, and closing the tab closes the shell.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 400; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
let root: Root | null = null;
let box: HTMLElement;
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  delete (window as { handline?: unknown }).handline;
  location.hash = '';
});

async function addTerminal() {
  await click(box.querySelector('button[aria-label="Add a tab"]'));
  await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((b) => b.textContent?.includes('Terminal')));
}

describe('the Terminal item of the Add a tab menu', () => {
  it('opens a shell per click, even while the agent has its own terminal, and closing the tab closes the shell', async () => {
    const s = setup();
    Object.assign(window, { handline: { api: 1, screen: async () => ({ ok: true }), unsent: async () => ({ ok: true }) } });
    location.hash = `#/agents/${ARCH}`;
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
    });
    await until(() => box.querySelector('button[aria-label="Add a tab"]'), 'the tab strip');
    await addTerminal();
    await until(() => s.daemon.script.shells.size === 1, 'the first shell');
    await addTerminal();
    await until(() => s.daemon.script.shells.size === 2, 'a second shell, not the first again');
    await until(() => $$('.xterm-host > div').length >= 1, 'the screen');
    expect($$('.xterm-host > div')).toHaveLength(1); // the first shell's screen does not stay under the second's
    const tabs = $$('[data-tab^="shell:"]');
    expect(tabs.map((t) => t.dataset.tab)).toEqual(['shell:%sh1', 'shell:%sh2']);
    expect(tabs.map((t) => t.textContent)).toEqual(['Terminal', 'Terminal']);
    await click(tabs[0]!.querySelector('button.x'));
    await until(() => s.daemon.script.shells.size === 1, 'the closed tab closes its shell');
    expect([...s.daemon.script.shells.keys()]).toEqual(['%sh2']);
    expect($$('[data-tab^="shell:"]')).toHaveLength(1);
  });
});

describe('a shell through the client', () => {
  it('takes keys and a size from anyone, with no input to take first', async () => {
    const s = setup();
    await s.client.start();
    const { terminal } = await s.client.call('shell.open', { agent: ARCH });
    const heard: string[] = [];
    const { view } = await s.client.bytes.openPane(terminal, (e) => e.type === 'write' && heard.push(new TextDecoder().decode(e.data)));
    await s.client.bytes.input(view, new TextEncoder().encode('ls\r'));
    await until(() => heard.join('').includes('ls'), 'the echo');
    await s.client.bytes.resize(view, 60, 10); // under the agent terminal's floor of 80 by 24
    await expect(s.client.call('shell.close', { terminal: 'term:' + ARCH })).rejects.toThrow(); // the agent's own terminal is not a shell
  });
});
