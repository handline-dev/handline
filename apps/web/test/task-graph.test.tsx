// The graph view of the Tasks page: boxes in Done, Running and Queued bands from the fake daemon's tasks.
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

const mount = async (s = setup(), hash = '#/tasks/graph') => {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  return s;
};
const band = (key: string) => [...box.querySelectorAll<HTMLElement>(`[data-node][data-band="${key}"]`)].map((n) => Number(n.dataset.node));

it('draws Running and Queued bands, keeps the top bar, and goes to a task on click', async () => {
  await mount();
  await until(() => box.querySelector('[data-node]'), 'the boxes');
  expect(band('running')).toEqual([1684, 1688]);
  expect(band('queued').sort()).toEqual([1690, 1700]);
  expect(box.textContent).toContain('handline.architect'); // a column per owner
  expect(box.querySelector('[aria-label="Show"]')).not.toBeNull(); // the Open | Mine | Closed pills stay
  expect([...box.querySelectorAll('button')].some((b) => b.textContent!.startsWith('View: Graph'))).toBe(true);
  expect(band('done')).toEqual([]); // closed long ago
  await act(async () => void box.querySelector<HTMLElement>('[data-node="1684"]')!.click());
  expect(location.hash).toBe('#/tasks/1684');
});

it('puts a task closed in the last 24 h in the Done band, and moves a box to the next band when its task changes', async () => {
  const s = setup();
  s.daemon.tasks.verbs = true;
  const rows = (s.daemon.tasks as unknown as { rows: { id: number; done: string | null }[] }).rows;
  rows.find((r) => r.id === 1650)!.done = new Date(Date.now() - 3_600_000).toISOString();
  await mount(s);
  await until(() => box.querySelector('[data-node]'), 'the boxes');
  expect(band('done')).toEqual([1650]);
  expect(band('queued')).toContain(1690);
  await s.client.start();
  await s.client.call('task.draft', { tasks: [1690], request_id: 'g1' }).catch(() => undefined);
  await s.client.call('task.done', { task: 1688, evidence: 'ok', reason: true, request_id: 'g2' });
  await until(() => band('done').includes(1688), 'the box in the Done band');
  expect(band('running')).toEqual([1684]);
});
