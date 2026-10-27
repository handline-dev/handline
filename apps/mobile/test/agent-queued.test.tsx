// An agent row shows how many tasks wait behind the current one; a tap lists them. Nothing shows at 0.
import { describe, expect, it } from 'vitest';
import { $, mount, press, until } from '#test/harness.tsx';

const ARCH = 'handline.architect'; // holds T1684
/** The architect holds T1684: 4 owned tasks are counted (it, a queued one, a second one doing, one more), a draft is not. */
const tasks = (s: Parameters<Parameters<typeof mount>[0] & object>[0]) => {
  const t = (id: number, state: 'doing' | 'queued' | 'draft', title: string) =>
    s.daemon.tasks.seed({ id, from: 'founder', to: ARCH, title, state });
  t(1684, 'doing', 'Web UI design');
  t(1685, 'queued', 'Second plan');
  t(1686, 'doing', 'Also under way');
  t(1687, 'queued', 'Fourth one');
  t(1688, 'draft', 'Not approved yet');
};
const queue = (counts: Record<string, number>) => (s: Parameters<Parameters<typeof mount>[0] & object>[0]) => {
  for (const [name, n] of Object.entries(counts)) {
    const session = s.daemon.sessions.get(name)!;
    session.fx = { ...session.fx, view: { ...session.fx.view, task_count: n } };
  }
};

describe('queued tasks on an agent row', () => {
  it('shows the tasks behind the current one, nothing at 0, and lists them on a tap', async () => {
    const m = await mount((s) => (tasks(s), queue({ [ARCH]: 4, 'handline.auditor': 0 })(s)), undefined, 'founder');
    await until(() => $(`queued-${ARCH}`), 'the chip');
    expect($(`queued-${ARCH}`)!.textContent).toBe('3'); // 4 owned, one of them current
    expect($('queued-handline.auditor')).toBeNull();
    expect($('queued-handline.term-build')).toBeNull(); // no count served: no chip
    await press($(`queued-${ARCH}`));
    await until(() => $('queued-sheet'), 'the list');
    await until(() => $('queued-task-1685'), 'the waiting tasks');
    expect($('queued-sheet')!.textContent).toContain('3 waiting behind T1684');
    const lines = [...document.body.querySelectorAll('[data-testid^="queued-task-"]')].map((e) => e.getAttribute('data-testid'));
    expect(lines).toEqual(['queued-task-1687', 'queued-task-1686', 'queued-task-1685']); // newest first, the same three the count says: not the current one, not the draft
    m.store.stop();
  });
});
