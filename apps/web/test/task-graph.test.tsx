// The graph page: boxes from the fake daemon's open tasks, reached from the Tasks bar and back.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

it('draws the open tasks as boxes and goes to a task on click', async () => {
  const s = setup();
  location.hash = '#/tasks/graph';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  await until(() => box.querySelector('[data-node]'), 'the boxes');
  const ids = [...box.querySelectorAll<SVGElement>('[data-node]')].map((n) => Number(n.dataset.node));
  expect(ids).toContain(1684);
  expect(ids).not.toContain(1650); // done
  await act(
    async () => void box.querySelector<SVGElement>('[data-node="1684"]')!.dispatchEvent(new MouseEvent('click', { bubbles: true })),
  );
  expect(location.hash).toBe('#/tasks/1684');
});
