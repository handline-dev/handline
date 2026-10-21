// The rail's unread badge, the last request under an agent's name, and the All|Unread filter.
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
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}

let root: Root | null = null;
let box: HTMLElement;
let keep = () => {};
afterEach(async () => {
  keep();
  keep = () => {};
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

const rows = () => [...box.querySelectorAll('[data-agent]')].map((r) => r.getAttribute('data-agent'));
const pick = (label: string) => [...box.querySelectorAll<HTMLButtonElement>('.rail-read button')].find((b) => b.textContent?.startsWith(label))!;

describe('the rail unread marks', () => {
  it('shows the count and the last request, and the Unread filter keeps only agents with something unseen', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined); // the fixture is shared by every setup
    fx.view = { founder_unread: 3, last_to_founder: { text: 'Should the daemon relay it?\nMore under.', at: new Date().toISOString() } };
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
    await until(() => box.querySelector('[data-agent]'), 'the agent rows');
    const row = box.querySelector(`[data-agent="${ARCH}"]`)!;
    expect(row.querySelector('.unread')?.textContent).toBe('3');
    expect(row.querySelector('.row-sub')?.textContent).toBe('Should the daemon relay it?');
    expect(rows().length).toBeGreaterThan(1);
    expect(pick('Unread').textContent).toBe('Unread1');
    await act(async () => pick('Unread').click());
    expect(rows()).toEqual([ARCH]);
    await act(async () => pick('All').click());
    expect(rows().length).toBeGreaterThan(1);
  });
});
