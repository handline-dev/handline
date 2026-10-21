import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, until } from '#test/harness.tsx';

describe('Markdown in a chat message', () => {
  it('draws a heading, a list, bold and code, keeps a line break, and still links a #N', async () => {
    await mount(
      (s) => {
        s.daemon.pullRequests.add(306);
        s.daemon.chat.seed(ARCH, {
          from: ARCH,
          to: 'human',
          body: '## Done\n- **two** fixes in #306\n- run `hl ok`\nfirst\nsecond',
          at: '2031-02-01T00:00:00Z',
        });
      },
      undefined,
      'founder',
    );
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => document.querySelector('[data-testid^="pr-"]'), 'the pull request link');
    const body = document.querySelector('[data-testid^="pr-"]')!.closest('[data-testid^="body-"]')!;
    expect(body.querySelector('[role="heading"], h1, h2, h3, h4, h5, h6')?.textContent).toBe('Done');
    expect(body.textContent).toContain('•');
    expect(body.textContent).not.toContain('**');
    expect(body.textContent).not.toContain('`');
    expect(body.textContent).toContain('first\nsecond');
  });
});
