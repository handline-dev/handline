// The three worktree answers on the phone, on the fake daemon: the delete pop for work in a worktree, the decision the daemon opens when
// nobody was there to ask, and the disk card.
import { describe, expect, it, vi } from 'vitest';
import { setMachineNeeds } from '@handline/fake-daemon/helpers';
import { $, ARCH, mount, press, text, until } from '#test/harness.tsx';

const LEAF = 'handline.term-build';

describe('Delete agent with work in its worktree', () => {
  it('turns into the three answers, and the pick is sent with the delete again', async () => {
    const m = await mount(
      (s) =>
        s.daemon.knobs.refuse('agent.retire', 'dirty', `${LEAF} has work in its worktree.`, undefined, 1, {
          worktrees: [{ path: '/work/leaf', files_changed: 3, commits_ahead: 2 }],
        }),
      undefined,
      'founder',
      { role: 'founder' },
    );
    const call = vi.spyOn(m.store.client, 'call');
    await until(() => $(`agent-${LEAF}`), 'the rows');
    await press($(`agent-${LEAF}`));
    await until(() => $('verbs-open'), 'the page');
    await press($('verbs-open'));
    await press($('verb-advanced'));
    await press($('verb-delete'));
    await press($('sheet-submit'));
    await until(() => $('delete-held-save'), 'the three answers');
    expect(text()).not.toContain("Couldn't retire"); // the sheet asks: no error line
    expect(text($('delete-held-sheet'))).toContain(`${LEAF} has 3 unsaved files, 2 unpushed commits`);
    expect(m.daemon.sessions.has(LEAF)).toBe(true);
    expect(['save', 'keep', 'force'].map((a) => text($(`delete-held-${a}`)))).toEqual([
      'Save to a branch, then delete',
      'Delete, keep the worktree',
      'Delete and lose the files',
    ]);
    await press($('delete-held-save'));
    await until(() => !m.daemon.sessions.has(LEAF), 'the retire');
    const sent = call.mock.calls.filter((c) => c[0] === 'agent.retire').map((c) => (c[1] as { worktree?: string }).worktree);
    expect(sent).toEqual([undefined, 'save']);
    expect(m.store.notices.list().map((n) => n.text)).toContain(`${LEAF} deleted; its work is saved to a rescue branch`);
    m.store.stop();
  }, 20000);
});

describe('Delete and lose the files', () => {
  it('asks once more, and only the second press sends force', async () => {
    const m = await mount(
      (s) =>
        s.daemon.knobs.refuse('agent.retire', 'dirty', 'has work', undefined, 1, {
          worktrees: [{ path: '/work/leaf', files_changed: 1, commits_ahead: 0 }],
        }),
      undefined,
      'founder',
      { role: 'founder' },
    );
    const call = vi.spyOn(m.store.client, 'call');
    await until(() => $(`agent-${LEAF}`), 'the rows');
    await press($(`agent-${LEAF}`));
    await press($('verbs-open'));
    await press($('verb-advanced'));
    await press($('verb-delete'));
    await press($('sheet-submit'));
    await until(() => $('delete-held-force'), 'the answers');
    await press($('delete-held-force'));
    expect(text($('delete-held-sure'))).toBe('Delete for good? Uncommitted files are lost.');
    expect(call.mock.calls.filter((c) => c[0] === 'agent.retire')).toHaveLength(1);
    await press($('delete-held-sure-go'));
    await until(() => !m.daemon.sessions.has(LEAF), 'the retire');
    expect(call.mock.calls.filter((c) => c[0] === 'agent.retire').map((c) => (c[1] as { worktree?: string }).worktree)).toEqual([
      undefined,
      'force',
    ]);
    m.store.stop();
  }, 20000);
});

describe('the decision for a worktree that holds work', () => {
  const seed = (id: number) => (s: Parameters<Parameters<typeof mount>[0] & {}>[0]) => {
    s.daemon.tasks.verbs = true;
    s.daemon.tasks.seed({ id, from: ARCH, to: 'me', title: "Remove pc-chat's worktree?", state: 'doing', tags: ['worktree-removal'] });
    void s.daemon.inbox.add({
      kind: 'decision',
      agent: ARCH,
      title: "Remove pc-chat's worktree?",
      body: 'Path: /work/pc-chat\nBranch: pc-chat\n\nMember: pc-chat\n\nClose this task with the number as its answer',
      task: id,
    });
  };
  const said = (m: Awaited<ReturnType<typeof mount>>) => m.store.notices.list().map((n) => n.text);

  it('has Keep it filled first, Save, and Remove anyway as the danger one; the body shows the facts, not how to close by number', async () => {
    const m = await mount(seed(9401), undefined, 'founder', { opens: 'auto' });
    await until(() => $('request-item:6'), 'the row');
    await press($('request-item:6'));
    await until(() => $('request-act-worktree-keep'), 'the buttons');
    const order = ['keep', 'save', 'force'].map((a) => text($(`request-act-worktree-${a}`)));
    expect(order).toEqual(['Keep it', 'Save to a branch, then remove', 'Remove anyway']);
    expect(text($('request-page'))).toContain('Path: /work/pc-chat');
    expect(text($('request-page'))).not.toMatch(/Close this task with the number|Member:/);
    expect($('request-act-done')).toBeNull();
    await press($('request-act-worktree-keep'));
    await until(() => m.daemon.tasks.get({ task: 9401 }).state === 'done', 'the daemon');
    expect(m.daemon.tasks.get({ task: 9401 }).evidence).toBe('3');
    await until(() => said(m).includes("pc-chat's worktree stays"), 'the line');
    m.store.stop();
  }, 20000);

  it('asks once more before Remove anyway, and says what was done after it', async () => {
    const m = await mount(seed(9402), undefined, 'founder', { opens: 'auto' });
    await until(() => $('request-item:6'), 'the row');
    await press($('request-item:6'));
    await until(() => $('request-act-worktree-force'), 'the buttons');
    await press($('request-act-worktree-force'));
    expect(text($('request-answers'))).toContain('Remove for good? Uncommitted files are lost.');
    expect(m.daemon.tasks.get({ task: 9402 }).state).not.toBe('done');
    await press($('request-sure-cancel'));
    expect($('request-act-worktree-keep')).not.toBeNull();
    await press($('request-act-worktree-force'));
    await press($('request-sure'));
    await until(() => m.daemon.tasks.get({ task: 9402 }).state === 'done', 'the daemon');
    expect(m.daemon.tasks.get({ task: 9402 }).evidence).toBe('2 yes');
    await until(() => said(m).includes("pc-chat's worktree removed with its files"), 'the line');
    m.store.stop();
  }, 20000);

  it('says nothing was done when the daemon refuses, and the item stays', async () => {
    const m = await mount(
      (s) => (seed(9403)(s), s.daemon.knobs.refuse('task.done', 'state', 'not a worktree git knows')),
      undefined,
      'founder',
      { opens: 'auto' },
    );
    await until(() => $('request-item:6'), 'the row');
    await press($('request-item:6'));
    await until(() => $('request-act-worktree-keep'), 'the buttons');
    await press($('request-act-worktree-keep'));
    await until(() => said(m).some((t) => t.includes('not a worktree git knows')), 'the refusal');
    expect(said(m)).not.toContain("pc-chat's worktree stays");
    expect($('request-act-worktree-keep')).not.toBeNull();
    m.store.stop();
  }, 20000);
});

describe('the disk card', () => {
  it('opens to what the daemon says of the disk', async () => {
    const m = await mount(
      (s) => void setMachineNeeds(s, [{ kind: 'disk', since: '2026-10-30T08:00:00Z', text: 'Free space is 3 GB.' }]),
      undefined,
      'founder',
      { role: 'founder', opens: 'auto' },
    );
    await until(() => $('request-machine:disk'), 'the row');
    await press($('request-machine:disk'));
    expect(text($('request-page'))).toContain('Free space is 3 GB.');
    m.store.stop();
  }, 20000);
});
