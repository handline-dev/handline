// Key hints: <html data-keys="held"> while ⌘ or Ctrl is down, gone on release or when the window loses focus.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { useHeldKeys } from '#src/heldKeys.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Probe() {
  useHeldKeys();
  return null;
}
let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  root = null;
});
const press = (type: 'keydown' | 'keyup', init: KeyboardEventInit) => act(() => void window.dispatchEvent(new KeyboardEvent(type, init)));

describe('held keys', () => {
  it('marks the page while ⌘ is held and clears it on release, on Ctrl alike, and on blur', () => {
    const box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    act(() => root!.render(<Probe />));
    const held = () => document.documentElement.dataset.keys;
    expect(held()).toBeUndefined();
    press('keydown', { key: 'Meta', metaKey: true });
    expect(held()).toBe('held');
    press('keyup', { key: 'Meta', metaKey: false });
    expect(held()).toBeUndefined();
    press('keydown', { key: 'Control', ctrlKey: true });
    expect(held()).toBe('held');
    act(() => void window.dispatchEvent(new Event('blur')));
    expect(held()).toBeUndefined();
    box.remove();
  });
});
