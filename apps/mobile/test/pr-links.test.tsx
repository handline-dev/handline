import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, until } from './harness.tsx';

describe('#N in a message', () => {
  it('links to the pull request of the agent repo when it has that number, and is plain text otherwise', async () => {
    await mount(
      (s) => {
        s.daemon.pullRequests.add(306);
        s.daemon.chat.seed(ARCH, { from: ARCH, to: 'human', body: 'fixed in #306, thread #20', at: '2031-02-01T00:00:00Z' });
      },
      undefined,
      'founder',
    );
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => document.querySelector('[data-testid^="pr-"]'), 'the pull request link');
    const link = document.querySelector('[data-testid^="pr-"]')!;
    expect(link.textContent).toBe('#306');
    expect(document.querySelectorAll('[data-testid^="pr-"]').length).toBe(1);
  });
});
