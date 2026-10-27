// A task handed to an agent is drawn as the board's delivery in the phone's Chat, not as a message from the person.
import { describe, expect, it } from 'vitest';
import { $, ARCH, mount, press, text, until, pickTab } from '#test/harness.tsx';

describe('chat task delivery on the phone', () => {
  it('labels it "Task T# handed to <agent>" instead of a from → to line', async () => {
    const m = await mount((s) => {
      s.daemon.chat.seed(ARCH, {
        from: 'human',
        to: ARCH,
        body: 'Founder decisions one at a time',
        kind: 'task',
        tasks: [{ id: 91, title: 'Founder decisions' }],
      });
    });
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await pickTab('chat');
    await until(() => text().includes('Founder decisions one at a time'), 'the message');
    expect(text()).toContain(`Task T91 handed to ${ARCH}`); // local-ref-ok
    expect(text()).not.toContain(`human → ${ARCH}`);
    m.store.stop();
  });
});
