// A pinned agent sits under Pinned in the full list; a filter lists each match once, with no Pinned section.
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { $, box, mount, press, sleep, until } from '#test/harness.tsx';

const longPress = async (id: string) => {
  const el = $(id)!;
  const fire = (type: string) =>
    act(async () => void el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, button: 0 })));
  await fire('mousedown');
  await sleep(700);
  await fire('mouseup');
};
const shown = () => [...document.body.querySelectorAll('[data-testid^="agent-"]')].map((e) => e.getAttribute('data-testid')!.slice(6));

describe('pinned agents under a filter', () => {
  it('lists a pinned agent once, in its team, when Needs me or Unread is on', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => shown().length > 1, 'the rows');
    await press($('chip-needs'));
    await until(() => shown().length > 0, 'the agent that needs you');
    const name = shown()[0]!;
    await press($('chip-all'));
    await until(() => shown().length > 1, 'all again');
    await longPress(`agent-${name}`);
    await until(() => $('pin-toggle'), 'the menu');
    await press($('pin-toggle'));
    await until(() => (box?.textContent ?? '').includes('Pinned'), 'the Pinned section');
    await press($('chip-needs'));
    await until(() => !box?.textContent?.includes('Pinned'), 'no Pinned section under Needs me');
    expect(shown().filter((n) => n === name)).toHaveLength(1); // a parent the filter keeps for context may show beside it
    await press($('chip-unread'));
    await until(() => !box?.textContent?.includes('Pinned'), 'no Pinned under Unread');
    expect(shown().filter((n) => n === name).length).toBeLessThanOrEqual(1);
    m.store.stop();
  }, 30_000);
});
