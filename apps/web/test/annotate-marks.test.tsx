// The marks editor: no toolbar; a tap pins, a drag boxes, a loop marks an area, the memo opens at the mark, Undo is always there.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import type { Mark } from '@handline/app-core';
import { Annotator } from '#src/Annotate.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
let marks: Mark[] = [];
const svg = () => box.querySelector<SVGSVGElement>('svg[aria-label="Mark here"]')!;
const memo = () => box.querySelector<HTMLTextAreaElement>('textarea[aria-label="Comment"]');
const badges = () => [...box.querySelectorAll<HTMLButtonElement>('button[aria-label^="Comment "]')];
const undo = () => [...box.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === 'Undo (⌘Z)')!;
const click = (el: Element) => act(async () => void (el as HTMLElement).click());

/** The picture is 200 px wide and drawn 100 px wide: a screen pixel is two picture pixels. */
async function mount() {
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(<Annotator image={new Blob(['x'], { type: 'image/png' })} onChange={(_b, m) => (marks = m)} />));
  const img = box.querySelector('img')!;
  Object.defineProperty(img, 'naturalWidth', { value: 200 });
  Object.defineProperty(img, 'naturalHeight', { value: 200 });
  await act(async () => void img.dispatchEvent(new Event('load')));
  svg().getBoundingClientRect = () => ({
    left: 0,
    top: 0,
    width: 100,
    height: 100,
    right: 100,
    bottom: 100,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
  svg().setPointerCapture = () => undefined;
}
const gesture = (...ps: [number, number][]) =>
  act(async () => {
    const ev = (type: string, [x, y]: [number, number]) =>
      svg().dispatchEvent(Object.assign(new Event(type, { bubbles: true }), { clientX: x, clientY: y, pointerId: 1 }));
    ev('pointerdown', ps[0]!);
    ps.slice(1, -1).forEach((p) => ev('pointermove', p));
    ev('pointerup', ps.at(-1)!);
  });
const type = (el: HTMLTextAreaElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const enter = () =>
  act(async () => void memo()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })));
const ring = (cx: number, cy: number, r: number): [number, number][] =>
  Array.from({ length: 25 }, (_, i) => [cx + r * Math.cos((2 * Math.PI * i) / 24), cy + r * Math.sin((2 * Math.PI * i) / 24)]);

afterEach(async () => {
  await act(async () => root?.unmount());
  box?.remove();
  marks = [];
});

describe('memo marks', () => {
  it('has no toolbar, and Undo is there from the start', async () => {
    await mount();
    expect(box.querySelector('[role="toolbar"]')).toBeNull();
    expect(undo().hasAttribute('disabled')).toBe(true);
    expect(box.textContent).toContain('0 comments');
  });

  it('a tap pins a circle, a drag draws a box, a loop marks an area: each opens its memo at the mark', async () => {
    await mount();
    await gesture([20, 20], [20, 20]);
    expect(memo()).not.toBeNull();
    await type(memo()!, 'Tab label is cut off');
    await enter();
    expect(memo()).toBeNull();
    await gesture([50, 50], [80, 70]);
    await type(memo()!, 'This text is too small');
    await enter();
    await gesture(...ring(30, 75, 15));
    expect(marks.map((m) => m.kind)).toEqual(['pin', 'box', 'area']);
    expect(marks[1]!.rect).toEqual({ x: 100, y: 100, w: 60, h: 40 }); // picture pixels
    expect(badges().map((b) => b.textContent)).toEqual(['1', '2', '3']);
    expect(box.textContent).toContain('3 comments');
  });

  it('a number on a mark opens its memo again, with its words', async () => {
    await mount();
    await gesture([20, 20], [20, 20]);
    await type(memo()!, 'first');
    await gesture([60, 60], [60, 60]);
    await type(memo()!, 'second');
    await enter();
    expect(memo()).toBeNull();
    await click(badges()[0]!);
    expect(memo()!.value).toBe('first');
  });

  it('Undo takes back the last mark, and a memo with no words is not among the marks that have any', async () => {
    await mount();
    await gesture([20, 20], [20, 20]);
    await type(memo()!, 'kept');
    await enter();
    await gesture([60, 60], [60, 60]); // its memo stays empty
    expect(marks.map((m) => m.text)).toEqual(['kept', '']);
    await click(undo());
    expect(marks.map((m) => m.text)).toEqual(['kept']);
    expect(badges()).toHaveLength(1);
    await click(undo());
    expect(badges()).toHaveLength(0);
    expect(undo().hasAttribute('disabled')).toBe(true);
  });

  it('the memo box deletes its mark', async () => {
    await mount();
    await gesture([20, 20], [20, 20]);
    await click([...box.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === 'Delete comment')!);
    expect(badges()).toHaveLength(0);
    expect(memo()).toBeNull();
  });
});
