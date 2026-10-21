import { describe, expect, it } from 'vitest';
import { ARCH, $, until, press, mount, pickTab } from '#test/harness.tsx';

describe('an agent with no terminal', () => {
  it('opens its page, offers Start terminal in the terminal tab, and shows the terminal once it runs', async () => {
    const m = await mount((s) => {
      Object.assign(s.daemon.sessions.get(ARCH)!, { open: false, hasTerminal: false });
    });
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('verbs-open'), 'the page opens');
    expect(document.body.textContent).not.toMatch(/no terminal/i);
    await pickTab('terminal');
    await until(() => $('terminal-start'), 'the Start terminal button');
    await press($('terminal-start'));
    await until(() => $('terminal-text'), 'the terminal after starting', 8000);
    m.store.stop();
  }, 20000);
});
