// The "new" count of a task-card fold: cards present when the chat loads are not new; later ones are, until the line is opened.
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import type { TaskFold } from '@handline/app-core';
import { useFolds } from '#src/ChatFold.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fold = (id: number, n: number) =>
  ({ id, cards: Array.from({ length: n }, (_, i) => `2026-01-01T10:0${i}:00Z`) }) as unknown as TaskFold;

it('counts only cards that join after the chat loaded, and opening the line clears them', () => {
  let api!: ReturnType<typeof useFolds>;
  let drawn = false;
  const Probe = ({ ready }: { ready: boolean }) => {
    api = useFolds({ status: ready ? 'ready' : 'loading', messages: [], items: [], moreMessages: false, moreItems: false }, 'mine', 'a', '', []);
    if (!ready || !drawn) api.fresh(fold(1, 3)); // drawn in the render that shows the loaded chat, before its effects run
    drawn = true;
    return null;
  };
  const root = createRoot(document.createElement('div'));
  act(() => root.render(<Probe ready />));
  expect(api.fresh(fold(1, 3))).toBe(0); // there at load
  act(() => root.render(<Probe ready />)); // a later render does not turn it into news
  expect(api.fresh(fold(1, 3))).toBe(0);
  expect(api.fresh(fold(1, 5))).toBe(2); // two joined
  expect(api.fresh(fold(2, 3))).toBe(3); // a run that appeared after the load
  act(() => api.toggle(fold(2, 3)));
  expect(api.fresh(fold(2, 3))).toBe(0);
  act(() => root.unmount());
});
