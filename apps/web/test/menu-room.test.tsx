// A menu is kept whole until neither side of its anchor holds it: its max height is the larger side, not the side Radix picked first.
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it } from 'vitest';
import { MenuButton, MenuItem } from '#src/ui/index.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let box: HTMLElement | null = null;
afterEach(() => {
  box?.remove();
  document.body.replaceChildren();
});

it('limits the panel to the larger side of the button, minus the window padding', async () => {
  window.innerHeight = 420;
  const at = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function () {
    return this instanceof HTMLSpanElement && this.className.includes('inline-flex')
      ? DOMRect.fromRect({ x: 10, y: 330, width: 40, height: 24 })
      : at.call(this);
  };
  try {
    box = document.body.appendChild(document.createElement('div'));
    const root = createRoot(box);
    const draw = (open: boolean) =>
      act(async () => {
        root.render(
          <MenuButton label="More" name="More" open={open}>
            <MenuItem>One</MenuItem>
          </MenuButton>,
        );
      });
    await draw(false);
    await draw(true);
    const menu = document.querySelector<HTMLElement>('[role=menu]');
    // above the button there are 330 px, below 66: the panel may take the larger.
    expect(menu?.style.maxHeight).toBe('318px');
  } finally {
    Element.prototype.getBoundingClientRect = at;
  }
});
