// The list primitive: an added item mounts at height 0 and a removed one shrinks before it leaves.
import { act, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';

vi.hoisted(() => {
  window.matchMedia = ((q: string) => ({
    matches: false,
    media: q,
    addEventListener() {},
    removeEventListener() {},
  })) as unknown as typeof window.matchMedia;
});

import { AnimatedItem, AnimatedList } from '../src/AnimatedList.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const list = (ids: number[]): ReactNode => (
  <AnimatedList>
    {ids.map((i) => (
      <AnimatedItem key={i} data-id={String(i)}>
        row {i}
      </AnimatedItem>
    ))}
  </AnimatedList>
);

/** Which items had a style of height 0 before one of their changes: a record's old value, so no frame is missed to timing. */
function watching(host: HTMLElement) {
  const closed = new Set<string>();
  const watch = new MutationObserver((records) => {
    for (const r of records)
      if (r.attributeName === 'style' && r.oldValue?.includes('height: 0px')) closed.add((r.target as HTMLElement).dataset.id ?? '');
  });
  watch.observe(host, { attributes: true, attributeOldValue: true, subtree: true });
  return { closedBefore: (id: string) => closed.has(id), stop: () => watch.disconnect() };
}

describe('AnimatedList', () => {
  it('mounts an added item at height 0 and keeps a removed one until it has shrunk', async () => {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    const ids = () => [...host.querySelectorAll('[data-id]')].map((e) => e.getAttribute('data-id'));
    await act(async () => root.render(list([1, 2])));
    const watch = watching(host);
    await act(async () => root.render(list([1, 2, 3])));
    await act(() => new Promise((r) => setTimeout(r, 800)));
    expect(watch.closedBefore('3')).toBe(true);
    expect((host.querySelector('[data-id="3"]') as HTMLElement).style.height).toBe('auto');
    watch.stop();
    await act(async () => root.render(list([1, 3])));
    expect(ids()).toContain('2');
    await act(() => new Promise((r) => setTimeout(r, 800)));
    expect(ids()).toEqual(['1', '3']);
  });

  it('calls onFrame while an item grows, and an instant item does not start at height 0', async () => {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    let frames = 0;
    const view = (ids: number[], instant: number) => (
      <AnimatedList>
        {ids.map((i) => (
          <AnimatedItem key={i} data-id={String(i)} instant={i === instant} onFrame={() => frames++}>
            row {i}
          </AnimatedItem>
        ))}
      </AnimatedList>
    );
    await act(async () => root.render(view([1], 0)));
    const watch = watching(host);
    await act(async () => root.render(view([1, 2, 3], 3)));
    await act(() => new Promise((r) => setTimeout(r, 800)));
    watch.stop();
    expect(frames).toBeGreaterThan(0);
    expect(watch.closedBefore('2')).toBe(true); // item 2 started closed
    expect(watch.closedBefore('3')).toBe(false); // item 3 never was
  });
});
