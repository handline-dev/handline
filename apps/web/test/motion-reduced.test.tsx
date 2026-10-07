// Under reduced motion (the test setup's default) a removed item is gone at once and an added one is not clipped.
import { act, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vitest';
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

describe('AnimatedList under reduced motion', () => {
  it('adds and removes without an animation', async () => {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    const ids = () => [...host.querySelectorAll('[data-id]')].map((e) => e.getAttribute('data-id'));
    await act(async () => root.render(list([1, 2])));
    await act(async () => root.render(list([1, 2, 3])));
    expect((host.querySelector('[data-id="3"]') as HTMLElement).style.height).toBe('');
    await act(async () => root.render(list([1, 3])));
    expect(ids()).toEqual(['1', '3']);
  });
});
