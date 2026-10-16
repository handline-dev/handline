// The capture's markup editor: tools draw shapes over the picture, Undo and Clear take them back, and a task can be picked to comment on.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Annotator } from '../src/Annotate.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<SVGSVGElement & HTMLElement>(sel)!;
const button = (label: string) =>
  [...box.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === label || b.textContent === label)!;
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const drag = (from: [number, number], to: [number, number]) =>
  act(async () => {
    const svg = $('svg');
    svg.getBoundingClientRect = () => ({
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
    svg.setPointerCapture = () => undefined;
    const ev = (type: string, [x, y]: [number, number]) =>
      svg.dispatchEvent(Object.assign(new Event(type, { bubbles: true }), { clientX: x, clientY: y, pointerId: 1 }));
    ev('pointerdown', from);
    ev('pointermove', to);
    ev('pointerup', to);
  });

async function mount(onChange: (b: Blob) => void = () => undefined) {
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(<Annotator image={new Blob(['x'], { type: 'image/png' })} onChange={onChange} />));
}
afterEach(async () => {
  await act(async () => root?.unmount());
  box?.remove();
});

describe('the markup editor', () => {
  it('draws a circle, an arrow and freehand, and Undo and Clear take them back', async () => {
    await mount();
    expect(button('Undo').hasAttribute('disabled')).toBe(true);
    await click(button('Circle'));
    await drag([10, 10], [60, 60]);
    expect(box.querySelectorAll('svg path')).toHaveLength(1);
    await click(button('Arrow'));
    await drag([0, 0], [90, 0]);
    await click(button('Pen'));
    await drag([5, 5], [20, 30]);
    expect(box.querySelectorAll('svg path')).toHaveLength(3);
    await click(button('Undo'));
    expect(box.querySelectorAll('svg path')).toHaveLength(2);
    await click(button('Redo'));
    expect(box.querySelectorAll('svg path')).toHaveLength(3);
    await click(button('Clear'));
    expect(box.querySelectorAll('svg path')).toHaveLength(0);
  });

  it('puts text where it is clicked, on Enter', async () => {
    await mount();
    await click(button('Text'));
    await act(async () => {
      const svg = $('svg');
      svg.getBoundingClientRect = () => ({
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
      const ev = (type: string) =>
        svg.dispatchEvent(Object.assign(new Event(type, { bubbles: true }), { clientX: 20, clientY: 20, pointerId: 1 }));
      ev('pointerdown');
      // A real click moves focus to what was clicked after the press: nothing may be open yet to lose it.
      expect(box.querySelector('input[aria-label="Text on the picture"]')).toBeNull();
      (document.activeElement as HTMLElement | null)?.blur();
      ev('pointerup');
    });
    expect(box.querySelector('input[aria-label="Text on the picture"]')).not.toBeNull();
    const field = box.querySelector('input[aria-label="Text on the picture"]') as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, 'too wide');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => void field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    expect(box.querySelector('svg text')?.textContent).toBe('TOO WIDE');
  });
});
