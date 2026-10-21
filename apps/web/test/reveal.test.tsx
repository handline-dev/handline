// What opens and closes moves height and opacity only, never a transform: a Reveal, and a Sheet under an <AnimatedList>.
import { act, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';

vi.hoisted(() => {
  window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
});

import { AnimatedList } from '#src/AnimatedList.tsx';
import { Reveal, Sheet } from '#src/ui/index.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** Every inline style the element ever had while it opened and closed, from the mutation records. */
function styles(host: HTMLElement) {
  const seen: string[] = [];
  const watch = new MutationObserver((rs) => rs.forEach((r) => seen.push((r.target as HTMLElement).getAttribute('style') ?? '')));
  watch.observe(host, { attributes: true, attributeFilter: ['style'], subtree: true });
  return seen;
}

const wait = () => act(() => new Promise((r) => setTimeout(r, 700)));

async function openAndClose(view: (open: boolean) => ReactNode, probe: string) {
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(view(false)));
  const seen = styles(host);
  await act(async () => root.render(view(true)));
  await wait();
  const shown = host.querySelector(probe) as HTMLElement;
  expect(shown.style.height).toBe('auto');
  await act(async () => root.render(view(false)));
  expect(host.querySelector(probe)).not.toBeNull(); // still there while it shrinks
  await wait();
  expect(host.querySelector(probe)).toBeNull();
  expect(seen.some((s) => s.includes('height: 0px'))).toBe(true);
  expect(seen.some((s) => /transform|scale|translate/.test(s))).toBe(false);
  root.unmount();
}

describe('open and close', () => {
  it('a Reveal grows from height 0 and shrinks back, with no transform', async () => {
    await openAndClose(
      (open) => (
        <Reveal show={open} className="probe">
          body
        </Reveal>
      ),
      '.probe',
    );
  });

  it('a Sheet grows and shrinks the same way under an AnimatedList', async () => {
    await openAndClose(
      (open) => <AnimatedList>{open ? <Sheet key="s">body</Sheet> : null}</AnimatedList>,
      '.verb-sheet',
    );
  });
});
