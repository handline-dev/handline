// The rail's unread badge and the last request under an agent's name.
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

describe('the rail unread marks', () => {
  it('shows the count and the last request', async () => {
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
    expect(row.querySelector('.row-line')?.textContent).toBe('Should the daemon relay it?');
    expect(row.querySelector('.row-time')).not.toBeNull(); // when it was said
    expect(row.querySelector('[aria-label="Context window"]')).toBeNull(); // no bars on the row
    expect(rows().length).toBeGreaterThan(1);
  });
});
