// An agent row shows how many tasks wait behind the current one; a tap lists them. Nothing shows at 0.
import { describe, expect, it } from 'vitest';
import { $, mount, press, until } from '#test/harness.tsx';

const ARCH = 'handline.architect'; // holds T1684
const queue = (counts: Record<string, number>) => (s: Parameters<Parameters<typeof mount>[0] & object>[0]) => {
  for (const [name, n] of Object.entries(counts)) {
    const session = s.daemon.sessions.get(name)!;
    session.fx = { ...session.fx, view: { ...session.fx.view, task_count: n } };
  }
};

describe('queued tasks on an agent row', () => {
  it('shows the tasks behind the current one, nothing at 0, and lists them on a tap', async () => {
    const m = await mount(queue({ [ARCH]: 4, 'handline.auditor': 0 }), undefined, 'founder');
    await until(() => $(`queued-${ARCH}`), 'the chip');
    expect($(`queued-${ARCH}`)!.textContent).toBe('+3'); // 4 owned, one of them current
    expect($('queued-handline.auditor')).toBeNull();
    expect($('queued-handline.term-build')).toBeNull(); // no count served: no chip
    await press($(`queued-${ARCH}`));
    await until(() => $('queued-sheet'), 'the list');
    expect($('queued-sheet')!.textContent).toContain('3 queued behind T1684');
    m.store.stop();
  });
});
