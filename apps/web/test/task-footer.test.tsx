import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
const data = new Map<string, string>();
const storage: KV = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await act(async () => void (await new Promise((r) => setTimeout(r, 10))));
  }
  throw new Error(`timed out waiting for ${what}`);
}
async function mount(hash: string, s = setup()) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={storage} />);
  });
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('the task detail footer', () => {
  it('is a solid bar stuck to the true bottom edge of the scroll box, so nothing shows beneath it', async () => {
    const s = setup();
    s.daemon.tasks.verbs = true;
    await mount('#/tasks/1688', s);
    await until(() => box.querySelector('.task-footer'), 'the footer');
    const scroll = box.querySelector('.tdetail')!;
    const bar = box.querySelector('.task-footer')!;
    // the scroll box pads its bottom by 12 px (py-3): the bar must reach under that padding, not stop above it
    expect(scroll.className).toContain('py-3');
    expect(bar.className).toMatch(/(^| )-bottom-3( |$)/);
    expect(bar.className).toContain('-mb-3');
    expect(bar.className).toContain('bg-inherit');
    expect(bar.className).toContain('border-t');
  });
});
