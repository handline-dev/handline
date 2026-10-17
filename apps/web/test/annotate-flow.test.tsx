// The markup editor's one-chip colour, undo from the keyboard and the tool keys.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Annotator } from '../src/Annotate.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
const button = (label: string) => [...box.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === label)!;
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const key = (k: string, init: KeyboardEventInit = {}) =>
  act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: k, ...init })));
const swatches = () => box.querySelectorAll('[role="group"][aria-label="Colours"] button');

afterEach(async () => {
  await act(async () => root?.unmount());
  box?.remove();
});

describe('markup controls', () => {
  it('shows one colour chip that opens five swatches, and picking one closes them', async () => {
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<Annotator image={new Blob(['x'], { type: 'image/png' })} onChange={() => undefined} />));
    expect(swatches()).toHaveLength(0);
    await click(button('Colour'));
    expect(swatches()).toHaveLength(5);
    await click(swatches()[3]!);
    expect(swatches()).toHaveLength(0);
    expect(button('Colour').style.background).toBe('#2f6bff');
  });

  it('switches tools with A R P T N, and leaves the letters alone while typing', async () => {
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<Annotator image={new Blob(['x'], { type: 'image/png' })} onChange={() => undefined} />));
    for (const [k, label] of [
      ['r', 'Rectangle (R)'],
      ['p', 'Pen (P)'],
      ['t', 'Text (T)'],
      ['a', 'Arrow (A)'],
    ] as const) {
      await key(k);
      expect(button(label).getAttribute('aria-pressed')).toBe('true');
    }
    const field = box.appendChild(document.createElement('input'));
    await act(async () => void field.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true })));
    expect(button('Arrow (A)').getAttribute('aria-pressed')).toBe('true');
  });
});
