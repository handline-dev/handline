// An agent the daemon holds for machine load: its chat says so, with Start now, until the work starts.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
async function until(ok: () => unknown, what: string) {
  for (let t = 0; t < 3000; t += 10) {
    if (ok()) return;
    await act(async () => void (await new Promise((r) => setTimeout(r, 10))));
  }
  throw new Error(`timed out waiting for ${what}`);
}
async function mount(held: boolean) {
  const s = setup();
  if (held)
    s.daemon.sessions.get(ARCH)!.fx.view = {
      status: {
        state: 'waiting',
        reason: 'load',
        text: 'Held: the Mac is busy (5.4 per core, limit 1.5). It starts by itself when that drops.',
        since: new Date().toISOString(),
        until: null,
        tasks: [],
      },
    };
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
  await until(() => $('.chat-foot'), 'the chat');
}

describe('chat of an agent held for machine load', () => {
  it('says why, and Start now starts it and removes the line', async () => {
    await mount(true);
    await until(() => $('.card-held'), 'the held line');
    expect($('.card-held')!.textContent).toContain('the Mac is busy (5.4 per core, limit 1.5)');
    const start = [...box.querySelectorAll('.card-held button')].find((b) => b.textContent === 'Start now') as HTMLElement;
    await act(async () => start.click());
    await until(() => !$('.card-held'), 'the line gone after the start');
  });

  it('shows nothing for an agent that is not held', async () => {
    await mount(false);
    await act(async () => void (await new Promise((r) => setTimeout(r, 300))));
    expect($('.card-held')).toBeNull();
  });
});
