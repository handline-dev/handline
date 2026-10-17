// The markup editor's pinned notes: a click or a drag opens a popover at that spot, and the words stay pinned to it and go out with the picture.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
    const svg = $('svg[aria-label="Draw here"]');
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

async function mount(onChange: (b: Blob, notes: string[]) => void = () => undefined) {
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(<Annotator image={new Blob(['x'], { type: 'image/png' })} onChange={onChange} />));
}
afterEach(async () => {
  await act(async () => root?.unmount());
  box?.remove();
});

const type = (el: HTMLTextAreaElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const popover = () => box.querySelector<HTMLTextAreaElement>('textarea[aria-label="Note"]');
const enter = (el: Element) =>
  act(async () => void el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })));

describe('pinned notes', () => {
  it('opens with the Note tool on', async () => {
    await mount();
    expect(button('Note').getAttribute('aria-pressed')).toBe('true');
    await drag([30, 30], [30, 30]);
    expect(popover()).not.toBeNull();
  });

  it('a click on the picture opens a popover there, and Enter pins the words and sends them with the picture', async () => {
    const sent: string[][] = [];
    await mount((_b, notes) => sent.push(notes));
    await click(button('Note'));
    await drag([30, 30], [30, 30]);
    expect(popover()).not.toBeNull();
    await type(popover()!, 'wrong colour');
    await enter(popover()!);
    expect(popover()).toBeNull();
    expect(button('Note 1: wrong colour')).toBeTruthy();
    expect(box.querySelector('svg[aria-label="Draw here"] text')?.textContent).toBe('1');
    await vi.waitFor(() => expect(sent.at(-1)).toEqual(['wrong colour']));
  });

  it('a drag marks a region, a note can be changed from its marker, and no words leave no note', async () => {
    await mount();
    await click(button('Note'));
    await drag([10, 10], [60, 60]);
    await enter(popover()!); // nothing typed
    expect(box.querySelectorAll('[aria-label^="Note 1"]')).toHaveLength(0);
    await drag([10, 10], [60, 60]);
    await type(popover()!, 'too tight');
    await enter(popover()!);
    await click(button('Note 1: too tight'));
    await type(popover()!, 'too loose');
    await enter(popover()!);
    expect(button('Note 1: too loose')).toBeTruthy();
  });

  it('removing the only note leaves Undo on, and it brings the note back', async () => {
    await mount();
    await drag([30, 30], [30, 30]);
    await type(popover()!, 'wrong colour');
    await enter(popover()!);
    await click(button('Note 1: wrong colour'));
    await type(popover()!, '');
    await enter(popover()!);
    expect(box.querySelectorAll('[aria-label^="Note 1"]')).toHaveLength(0);
    expect(button('Undo').hasAttribute('disabled')).toBe(false);
    await click(button('Undo'));
    expect(button('Note 1: wrong colour')).toBeTruthy();
  });
});
