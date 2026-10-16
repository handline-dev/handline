// My turn: a task's card opens the task detail in a split beside the list, and the card's menu stays inside the window.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}

let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  vi.unstubAllGlobals();
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

async function open(wide: boolean) {
  const real = window.matchMedia;
  vi.stubGlobal('matchMedia', (q: string) =>
    q.includes('min-width') ? ({ matches: wide, addEventListener() {}, removeEventListener() {} } as unknown as MediaQueryList) : real(q),
  );
  const s = setup();
  s.daemon.tasks.verbs = true;
  s.daemon.tasks.seed({
    id: 9001,
    from: 'web-test',
    to: 'handline.architect',
    title: 'Report 9001',
    state: 'reported',
    report: { by: 'handline.architect', evidence: 'built', at: new Date().toISOString() },
  });
  location.hash = '#/my-turn';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  await until(() => box.querySelector('[data-card="accept:9001"]'), 'the accept card');
  return s;
}

describe('my turn split', () => {
  it('a click on a task card opens its detail beside the list, and Back closes it', async () => {
    await open(true);
    await act(async () => void box.querySelector<HTMLElement>('[data-card="accept:9001"] h3')!.click());
    expect(location.hash).toBe('#/my-turn/9001');
    await until(() => box.querySelector('.tdetail'), 'the task detail');
    expect(box.querySelector('.my-turn-card')).not.toBeNull();
    expect(box.querySelector('[data-card="accept:9001"]')!.getAttribute('aria-current')).toBe('true');
    await act(async () => void box.querySelector<HTMLElement>('.tdetail button.back')!.click());
    expect(location.hash).toBe('#/my-turn');
    expect(box.querySelector('.tdetail')).toBeNull();
  });

  it('a click on the card buttons does not open the detail', async () => {
    await open(true);
    await act(async () => void box.querySelector<HTMLElement>('[data-card="accept:9001"] button[aria-label="More"]')!.click());
    expect(location.hash).toBe('#/my-turn');
  });

  it('on a narrow window the detail replaces the list', async () => {
    await open(false);
    location.hash = '#/my-turn/9001';
    await until(() => box.querySelector('.tdetail'), 'the task detail');
    expect(box.querySelector('.my-turn-card')).toBeNull();
  });

  it('the More menu is a flipping menu outside the scrolling list', async () => {
    await open(true);
    await act(async () => void box.querySelector<HTMLElement>('[data-card="accept:9001"] button[aria-label="More"]')!.click());
    const menu = document.querySelector('[role=menu]')!;
    expect(menu).not.toBeNull();
    expect(box.querySelector('.my-turn')!.contains(menu)).toBe(false);
  });
});
