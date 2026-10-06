import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { rowHeight } from '@handline/tokens';
import {
  SKELETON_CSS,
  SKELETON_DELAY_MS,
  SKELETON_MIN_MS,
  Skeleton,
  skeletons,
  useSkeletonGate,
  type BoxComponent,
  type SkeletonNode,
} from '../src/index.ts';
import { Probe, mount, openStore, stubClient, unmount } from './harness.tsx';

afterEach(async () => {
  vi.useRealTimers();
  await unmount();
});

const advance = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

describe('the skeleton gate: 150 ms before it shows, 300 ms at least once shown', () => {
  async function gate() {
    vi.useFakeTimers();
    const state = { loading: true };
    const out: { current: boolean | undefined } = { current: undefined };
    const g = { get: () => out.current };
    const el = document.body.appendChild(document.createElement('div'));
    const root = createRoot(el);
    // A new element each time, so React draws it again and the hook reads the new `loading`.
    const render = () => act(async () => root.render(<Probe use={() => useSkeletonGate(state.loading)} out={out} />));
    await render();
    return { g, state, render, done: () => act(async () => root.unmount()) };
  }

  it('shows nothing before the delay', async () => {
    const { g, done } = await gate();
    await advance(SKELETON_DELAY_MS - 10);
    expect(g.get()).toBe(false);
    await advance(20);
    expect(g.get()).toBe(true);
    await done();
  });

  it('a read that answers in time shows no skeleton at all', async () => {
    const { g, state, render, done } = await gate();
    await advance(100);
    state.loading = false;
    await render();
    await advance(1000);
    expect(g.get()).toBe(false);
    await done();
  });

  it('once shown it stays for the minimum, then goes', async () => {
    const { g, state, render, done } = await gate();
    await advance(SKELETON_DELAY_MS + 10);
    expect(g.get()).toBe(true);
    await advance(40);
    state.loading = false;
    await render();
    await advance(SKELETON_MIN_MS - 100);
    expect(g.get()).toBe(true);
    await advance(100);
    expect(g.get()).toBe(false);
    await done();
  });

  it('data that arrives after the minimum replaces it at once', async () => {
    const { g, state, render, done } = await gate();
    await advance(SKELETON_DELAY_MS + SKELETON_MIN_MS + 50);
    state.loading = false;
    await render();
    await advance(5);
    expect(g.get()).toBe(false);
    await done();
  });
});

/** The height of one row of a shape: its own `h`, or the sum of what it holds. */
const heightOf = (n: SkeletonNode): number =>
  'box' in n
    ? n.box.h
    : 'repeat' in n
      ? heightOf(n.of)
      : (n.h ?? (('row' in n ? n.row : n.col).map(heightOf).reduce((a, b) => a + b, 0) || 0));

describe('the shapes', () => {
  it('a list row is as tall as the real row, from the same tokens', () => {
    const rows = {
      agents: rowHeight.agent,
      team: rowHeight.agent,
      accounts: rowHeight.account,
      settings: rowHeight.field,
      chat: rowHeight.message,
      tasks: rowHeight.task,
      inbox: rowHeight.inbox,
    } as const;
    for (const [name, h] of Object.entries(rows)) expect(heightOf(skeletons[name as keyof typeof rows]), name).toBe(h);
  });

  it('every shape draws through the Box the app gives, with the shimmer class and no spinner', async () => {
    vi.useFakeTimers();
    const Box: BoxComponent = ({ style, children, ...rest }) => (
      <i data-custom {...rest} style={style as never}>
        {children}
      </i>
    );
    const view = (name: keyof typeof skeletons) => <Skeleton shape={name} loading label={name} />;
    for (const name of Object.keys(skeletons) as (keyof typeof skeletons)[]) {
      const { box } = await mount(openStore(stubClient()), view(name), undefined, Box);
      await advance(SKELETON_DELAY_MS + 1);
      expect(box.querySelector('[role="status"] [data-custom].sk'), name).not.toBeNull();
      expect(box.querySelector('[class*="spin"], progress, svg'), name).toBeNull();
      await unmount();
    }
  });

  it('the shimmer stops when the system asks for reduced motion', () => {
    expect(SKELETON_CSS).toMatch(/@media \(prefers-reduced-motion: reduce\) \{ \.sk \{ animation: none; \} \}/);
    expect(SKELETON_CSS).not.toMatch(/spin|rotate/);
  });
});
