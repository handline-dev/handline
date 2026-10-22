// A popup keeps to the window: it opens under its trigger, and above it when the trigger is at the bottom.
import { expect, it } from 'vitest';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Popup } from '#src/ui/index.ts';

const rect = (top: number): DOMRect => ({
  top,
  bottom: top + 20,
  left: 10,
  right: 50,
  width: 40,
  height: 20,
  x: 10,
  y: top,
  toJSON: () => ({}),
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function open(top: number) {
  const proto = HTMLElement.prototype;
  const was = proto.getBoundingClientRect;
  proto.getBoundingClientRect = () => rect(top);
  Object.defineProperty(proto, 'scrollHeight', { configurable: true, get: () => 300 });
  try {
    const host = document.createElement('div');
    document.body.append(host);
    await act(async () => {
      createRoot(host).render(
        <div style={{ position: 'relative' }}>
          <Popup open onClose={() => undefined} title="Filters">
            rows
          </Popup>
        </div>,
      );
    });
    return host.querySelector('.ui-popup')!;
  } finally {
    proto.getBoundingClientRect = was;
  }
}

it('opens below a trigger near the top and above one near the bottom', async () => {
  expect((await open(40)).classList.contains('up')).toBe(false);
  expect((await open(innerHeight - 40)).classList.contains('up')).toBe(true);
});
