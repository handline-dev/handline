// The pane bar's Open in menu: the desktop shell adds the apps it has, and a click sends only the app key and the agent.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
let root: Root | null = null;
let box: HTMLElement;
const trigger = () => [...box.querySelectorAll<HTMLElement>('button')].find((b) => b.textContent === 'Open in');
const items = () => [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].map((b) => b.textContent);

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  delete (window as { handline?: unknown }).handline;
  location.hash = '';
});

async function openMenu(shell?: object) {
  if (shell) Object.assign(window, { handline: { api: 1, screen: async () => ({ ok: true }), unsent: async () => ({ ok: true }), ...shell } });
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(setup().client)} daemon="test" storage={null} />);
  });
  for (let i = 0; i < 200 && !trigger(); i++) await wait();
  await wait(50);
  await act(async () => void trigger()!.click());
}

describe('the Open in menu', () => {
  it('shows only Copy path in a plain browser', async () => {
    await openMenu();
    expect(items()).toEqual(['Copy path']);
  });

  it('lists the apps the shell has and sends the app key and the agent on a click', async () => {
    const openIn = vi.fn(async () => ({ ok: true }));
    await openMenu({ openIn, openInApps: async () => ({ ok: true, apps: ['vscode', 'claude'] }) });
    expect(items()).toEqual(['Claude Code in Terminal', 'VS Code', 'Copy path']);
    await act(async () => void [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((b) => b.textContent === 'VS Code')!.click());
    expect(openIn).toHaveBeenCalledWith({ app: 'vscode', agent: ARCH });
  });
});
