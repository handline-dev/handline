import { afterEach, describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, text, until } from './harness.tsx';

const set = new Set<{ view?: unknown }>();
afterEach(() => {
  for (const fx of set) fx.view = undefined;
  set.clear();
});

describe('handoff line', () => {
  it('says why and what, and its age moves on', async () => {
    const m = await mount((s) => {
      const a = s.daemon.sessions.get(ARCH)!;
      set.add(a.fx);
      a.fx.view = { handoff: { cause: 'context_full', phase: 'writing', since: new Date(Date.now() - 5_000).toISOString() } };
    });
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('handoff-line'), 'the line');
    const first = text($('handoff-line'));
    expect(first).toContain('Context full');
    expect(first).toContain('Writing the handoff');
    await until(() => text($('handoff-line')) !== first, 'the age to move');
    m.store.stop();
  }, 20000);
});
