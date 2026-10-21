import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, text, until } from '#test/harness.tsx';

describe('the team screen', () => {
  it('shows how many tasks wait for an agent, and nothing for an agent with none', async () => {
    const m = await mount((s) => void (s.daemon.sessions.get(ARCH)!.fx.view = { task_count: 3 }), undefined, 'founder');
    await until(() => $('agents-menu'), 'the entry');
    await press($('agents-menu'));
    await press($('team-open'));
    await until(() => $(`team-queue-${ARCH}`), 'the queue line');
    expect(text($(`team-queue-${ARCH}`))).toBe('3 tasks waiting');
    expect(document.body.querySelectorAll('[data-testid^="team-queue-"]').length).toBe(1);
    m.store.stop();
  });
});
