// The contents list marks the section in view, also for a section that mounts after the page (a settings page waits for its read).
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ContentsPage } from '#src/ContentsPage.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PARTS = [
  { id: 'one', label: 'One' },
  { id: 'two', label: 'Two' },
];
let seen: Element[] = [];
let report: (target: Element) => void = () => undefined;
let root: Root | null = null;
let box: HTMLElement;

beforeEach(() => {
  seen = [];
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      constructor(cb: (hits: { target: Element; isIntersecting: boolean }[]) => void) {
        report = (target) => cb([{ target, isIntersecting: true }]);
      }
      observe = (el: Element) => void seen.push(el);
      disconnect = () => undefined;
    },
  );
});
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  vi.unstubAllGlobals();
});

let showSecond: () => void = () => undefined;
function Page() {
  const [late, setLate] = useState(false);
  showSecond = () => setLate(true);
  return (
    <ContentsPage parts={PARTS}>
      <section id="one">first</section>
      {late ? <section id="two">second</section> : null}
    </ContentsPage>
  );
}

describe('the contents list', () => {
  it('marks a section that mounted after the page once it comes into view', async () => {
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<Page />));
    const current = () => box.querySelector('nav [aria-current="location"]')?.textContent;
    expect(current()).toBe('One');
    expect(seen.map((e) => e.id)).toEqual(['one']);
    await act(async () => showSecond());
    expect(seen.map((e) => e.id)).toContain('two');
    await act(async () => report(box.querySelector('#two')!));
    expect(current()).toBe('Two');
  });
});
