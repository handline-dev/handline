// The sidebar row shows how many tasks wait behind the agent's current one, as +N, and nothing at 0.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect'; // holds T1684
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
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('queued tasks on a sidebar row', () => {
  it('shows +N for the tasks behind the current one and nothing at 0 or with no count', async () => {
    const s = setup();
    for (const [name, n] of Object.entries({ [ARCH]: 4, 'handline.auditor': 0 })) {
      const session = s.daemon.sessions.get(name)!;
      session.fx = { ...session.fx, view: { ...session.fx.view, task_count: n } };
    }
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
    const chip = (name: string) => box.querySelector(`[data-agent="${name}"] .row-queued`);
    await until(() => chip(ARCH), 'the chip');
    expect(chip(ARCH)!.textContent).toBe('+3');
    expect(chip(ARCH)!.getAttribute('title')).toContain('3 queued behind the current task');
    expect(chip('handline.auditor')).toBeNull();
    expect(chip('handline.term-build')).toBeNull();
  });
});
