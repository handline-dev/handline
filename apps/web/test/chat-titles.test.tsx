// A task card keeps its title when the task is older than the newest tasks the chat lists.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { applySeeds } from '@handline/fake-daemon/seeds';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('task card titles', () => {
  it('reads the title of a task older than the newest 200 by its id', async () => {
    const s = setup();
    applySeeds(s.daemon, 'tasks-200'); // 200 newer tasks: T1684 is no longer in the list's page
    location.hash = '#/agents/handline.architect';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
    let found = false;
    for (let i = 0; i < 300 && !found; i++) {
      await act(async () => void (await new Promise((r) => setTimeout(r, 10))));
      found = [...box.querySelectorAll('[data-kind="task_event"]')].some((c) => c.textContent?.includes('Web UI design'));
    }
    expect(found).toBe(true);
  });

  const wait = (ms: number) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
  const cards = () => [...box.querySelectorAll('[data-kind="task_event"]')];
  const open = async (s: ReturnType<typeof setup>) => {
    location.hash = '#/agents/handline.architect';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
  };

  it('draws no task card while the list of titles is on its way, then draws them titled', async () => {
    const s = setup();
    const release = s.daemon.knobs.hold('tasks.list');
    await open(s);
    await wait(500);
    expect(cards()).toHaveLength(0);
    release();
    await wait(500);
    expect(cards().length).toBeGreaterThan(0);
    expect(cards().every((c) => c.textContent?.includes('Web UI design'))).toBe(true);
  });

  it('draws no card either while the title of an older task is read by its id', async () => {
    const s = setup();
    applySeeds(s.daemon, 'tasks-200');
    const release = s.daemon.knobs.hold('task.get');
    await open(s);
    await wait(500);
    expect(cards()).toHaveLength(0);
    release();
    await wait(500);
    expect(cards().some((c) => c.textContent?.includes('Web UI design'))).toBe(true);
  });
});
