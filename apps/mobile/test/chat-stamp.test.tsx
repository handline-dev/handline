// A message's time and ticks sit beside its bubble, not inside it, and no message shows a number.
import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, until } from '#test/harness.tsx';

describe('the stamp of a chat message', () => {
  it('is beside the bubble, and no row shows a message number', async () => {
    await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => document.querySelector('[data-testid^="landing-"]'), 'a sent message');
    for (const row of document.querySelectorAll<HTMLElement>('[data-testid^="msg-"]')) {
      expect(row.textContent).not.toMatch(/#\d/);
      const tick = row.querySelector('[data-testid^="landing-"]');
      if (!tick) continue;
      const bubble = tick.parentElement!.previousElementSibling ?? tick.parentElement!.nextElementSibling;
      expect(bubble?.contains(tick)).toBe(false);
    }
  });
});
