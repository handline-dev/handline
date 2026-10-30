// The two-agent split: an agent row dragged onto the right half opens it beside (?beside= in the URL), onto the left half switches that pane;
// Back undoes a drop; keys reach only the half last clicked; a window too narrow refuses.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';
import { AGENT_DRAG } from '#src/SplitView.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const A = 'handline.architect';
const B = 'handline.auditor';
let box: HTMLElement;
let root: Root | null = null;
let width = 1200;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const widthOf = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')!;
beforeEach(() => {
  width = 1200;
  history.replaceState(null, '');
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => width });
});
afterEach(async () => {
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', widthOf);
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});
async function mount() {
  const store = new Store(setup().client);
  location.hash = `#/agents/${A}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={null} />);
  });
  await until(() => $(`[data-agent="${B}"]`) && $('[data-half="left"] .workspace'), 'the rows and the left half');
}
/** A drag of the agent's row onto a half, with the data transfer jsdom lacks. */
async function dragOnto(agent: string, half: 'left' | 'right') {
  const data = new Map<string, string>();
  const dataTransfer = {
    get types() {
      return [...data.keys()];
    },
    setData: (k: string, v: string) => void data.set(k, v),
    getData: (k: string) => data.get(k) ?? '',
    effectAllowed: '',
    dropEffect: '',
  };
  const fire = (el: Element, type: string) => {
    const e = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(e, 'dataTransfer', { value: dataTransfer });
    return act(async () => void el.dispatchEvent(e));
  };
  await fire($(`[data-agent="${agent}"]`)!, 'dragstart');
  expect(data.has(AGENT_DRAG)).toBe(true);
  await until(() => $(`[data-drop-half="${half}"]`), 'the half to drop on');
  await fire($(`[data-drop-half="${half}"]`)!, 'dragover');
  expect($(`[data-drop-half="${half}"]`)!.className).toContain('border-acc'); // the target half is lit
  await fire($(`[data-drop-half="${half}"]`)!, 'drop');
}
const halves = () => [...box.querySelectorAll('[data-half]')].map((h) => h.getAttribute('data-half'));

describe('the split by drag', () => {
  it('opens the agent beside on a drop on the right half', async () => {
    await mount();
    expect(halves()).toEqual(['left']);
    await dragOnto(B, 'right');
    await until(() => halves().length === 2, 'the second half');
    expect(location.hash).toBe(`#/agents/${encodeURIComponent(A)}?beside=${encodeURIComponent(B)}`);
  });

  it('switches the left pane on a drop on the left half and keeps the second agent', async () => {
    await mount();
    await dragOnto(B, 'right');
    await until(() => halves().length === 2, 'the second half');
    await dragOnto('handline.term-build', 'left');
    await until(() => location.hash.startsWith(`#/agents/${encodeURIComponent('handline.term-build')}`), 'the new left agent');
    expect(location.hash).toContain(`beside=${encodeURIComponent(B)}`);
  });

  it('swaps the halves when the dropped agent is the one in the other half', async () => {
    await mount();
    await dragOnto(B, 'right');
    await until(() => halves().length === 2, 'the second half');
    await dragOnto(B, 'left');
    await until(() => location.hash.startsWith(`#/agents/${encodeURIComponent(B)}`), 'the swap');
    expect(location.hash).toContain(`beside=${encodeURIComponent(A)}`);
  });

  it('the second half closes the split from its Split button', async () => {
    await mount();
    await dragOnto(B, 'right');
    await until(() => $('[aria-label="Close the split"]'), 'the close button');
    await act(async () => void $('[aria-label="Close the split"]')!.click());
    await until(() => halves().length === 1, 'one agent');
    expect(location.hash).toBe(`#/agents/${encodeURIComponent(A)}`);
  });

  it('sends window keys only to the half last clicked', async () => {
    await mount();
    await dragOnto(B, 'right');
    await until(() => $('[data-half="right"] .workspace'), 'the right workspace');
    const key = () =>
      act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: 't', metaKey: true, bubbles: true, cancelable: true })));
    await act(async () => void $('[data-half="right"]')!.dispatchEvent(new Event('pointerdown', { bubbles: true })));
    await key();
    await until(() => $('[data-half="right"] .new-tab'), 'a new tab in the right half');
    expect($('[data-half="left"] .new-tab')).toBeNull();
  });

  it('refuses a drop on the right half when the window is too narrow', async () => {
    width = 500;
    await mount();
    await dragOnto(B, 'right');
    expect(halves()).toEqual(['left']);
    expect(location.hash).toBe(`#/agents/${encodeURIComponent(A)}`);
  });
});
