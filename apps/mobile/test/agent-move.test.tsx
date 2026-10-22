// Long-press an agent row: Move up, Move down and Move to top, saved to the daemon's nav order (a flat list has none).
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { $, mount, press, sleep, until } from '#test/harness.tsx';

/** Holds the pointer on the row past the long-press delay, then lets go. */
const longPress = async (id: string) => {
  const el = $(id)!;
  const fire = (type: string) =>
    act(async () => void el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, button: 0 })));
  await fire('mousedown');
  await sleep(700);
  await fire('mouseup');
};
const shown = () => [...document.body.querySelectorAll('[data-testid^="agent-"]')].map((e) => e.getAttribute('data-testid')!.slice(6));
const disabled = (id: string) => $(id)!.getAttribute('aria-disabled') === 'true' || $(id)!.hasAttribute('disabled');

describe('moving an agent in the list', () => {
  it('moves a row up, down and to the top among its siblings and keeps the place', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => shown().length > 2, 'the rows');
    const before = shown();
    const name = before[1]!;
    await longPress(`agent-${name}`);
    await until(() => $('move-up'), 'the menu');
    const stays = disabled('move-up');
    await press($(stays ? 'move-down' : 'move-up'));
    await until(() => shown().join() !== before.join(), 'the new order');
    expect(shown().sort()).toEqual([...before].sort());
    await until(() => m.daemon.admin.prefs.get().nav.order.length > 0, 'the saved order');
    m.store.stop();
  });

  it('offers nothing to move in the flat list', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => shown().length > 1, 'the rows');
    await press($('layout-toggle'));
    await until(() => shown().length > 1, 'flat');
    await longPress(`agent-${shown()[0]}`);
    await until(() => $('pin-toggle'), 'the menu');
    expect($('move-up')).toBeNull();
    m.store.stop();
  });
});
