import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, until } from './harness.tsx';

describe('a decided HR proposal in the phone chat', () => {
  it('is one line with its result, and an open one is not', async () => {
    const m = await mount((s) => {
      const done = s.daemon.raiseProposal({ id: 61, title: 'link a -> b', diff: 'link', agent: ARCH });
      s.daemon.script.hr.settle(done, 'approved');
      s.daemon.raiseProposal({ id: 62, title: 'context c', diff: 'replace', agent: ARCH });
    }, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('decided'), 'the decided line');
    expect($('decided')!.textContent).toContain('link a -> b');
    expect($('decided')!.textContent).toContain('Applied by you');
    expect(document.body.textContent).not.toContain('context c · ');
    m.store.stop();
  });
});
