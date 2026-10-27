// The sidebar row shows how many tasks wait behind the agent's current one, as the Tasks icon and a number, and nothing at 0.
import { readFileSync } from 'node:fs';
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
    const t = (id: number, state: 'doing' | 'queued' | 'draft', title: string) =>
      s.daemon.tasks.seed({ id, from: 'founder', to: ARCH, title, state });
    t(1684, 'doing', 'Web UI design'); // the current task
    t(1685, 'queued', 'Second plan');
    t(1686, 'doing', 'Also under way'); // counted too: the count is every task not done
    t(1687, 'queued', 'Fourth one');
    t(1688, 'draft', 'Not approved yet'); // not counted
    for (const [name, n] of Object.entries({ [ARCH]: 4, 'handline.auditor': 0 })) {
      const session = s.daemon.sessions.get(name)!;
      session.fx = { ...session.fx, view: { ...session.fx.view, task_count: n } };
    }
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
    const chip = (name: string) => box.querySelector(`[data-agent="${name}"] .row-queued`);
    await until(() => chip(ARCH), 'the chip');
    expect(chip(ARCH)!.textContent).toBe('3');
    expect(chip(ARCH)!.querySelector('[data-icon="tasks"]')).toBeTruthy(); // the Tasks icon, no ring
    expect(chip(ARCH)!.className).not.toMatch(/ring|rounded/);
    // The first line never wraps; the chips drop whole from the last one and the time sits outside them, so it keeps its place.
    const main = box.querySelector(`[data-agent="${ARCH}"] .row-main`)!;
    expect(main.className).not.toMatch(/flex-wrap/);
    expect(main.querySelector('.row-chips .row-queued')).toBeTruthy();
    expect(main.querySelector('.row-time')!.parentElement).toBe(main); // not among the chips
    await act(
      async () => void box.querySelector(`[data-agent="${ARCH}"]`)!.dispatchEvent(new MouseEvent('pointerover', { bubbles: true })),
    );
    await act(async () => void box.querySelector(`[data-agent="${ARCH}"]`)!.dispatchEvent(new PointerEvent('pointerenter')));
    await until(() => chip(ARCH)!.getAttribute('title')?.includes('T1685'), 'the list');
    const tip = chip(ARCH)!.getAttribute('title')!;
    expect(tip).toContain('3 waiting behind the current task');
    expect(tip.split('\n').slice(1)).toEqual(['T1687 Fourth one', 'T1686 Also under way', 'T1685 Second plan']);
    expect(chip('handline.auditor')).toBeNull();
    expect(chip('handline.term-build')).toBeNull();
  });
});

describe('the working count in a row', () => {
  it('turns its icon like the state glyph does, and stops for reduced motion', () => {
    const css = readFileSync('src/styles/ui.css', 'utf8');
    expect(css).toMatch(/\.row-working svg\s*\{\s*animation: ui-spin/);
    expect(css).toMatch(/prefers-reduced-motion[^}]*\.row-working svg/);
  });
});
