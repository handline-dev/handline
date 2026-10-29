// The three worktree answers on the phone, on the fake daemon: the delete pop for work in a worktree, the decision the daemon opens when
// nobody was there to ask, and the leftover worktrees under the disk card.
import { describe, expect, it, vi } from 'vitest';
import type { Worktree } from '@handline/client';
import { setMachineNeeds } from '@handline/fake-daemon/helpers';
import { $, ARCH, box, mount, press, text, until } from '#test/harness.tsx';

const LEAF = 'handline.term-build';
const orphan = (name: string, more: Partial<Worktree> = {}): Worktree => ({
  path: `/work/${name}`,
  repo: 'handline',
  branch: name,
  owner: null,
  parent: null,
  children: [],
  status: 'done',
  changed: [],
  orphan: true,
  merged: true,
  files_changed: 0,
  commits_ahead: 0,
  size_bytes: 2 ** 30,
  ...more,
});

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
    expect($('delete-held-force')).not.toBeNull();
    expect($('delete-held-keep')).not.toBeNull();
    await press($('delete-held-save'));
    await until(() => !m.daemon.sessions.has(LEAF), 'the retire');
    const sent = call.mock.calls.filter((c) => c[0] === 'agent.retire').map((c) => (c[1] as { worktree?: string }).worktree);
    expect(sent).toEqual([undefined, 'save']);
    m.store.stop();
  }, 20000);
});

describe('the decision for a worktree that holds work', () => {
  it('has the three buttons, and each sends its word as the answer', async () => {
    const m = await mount((s) => {
      s.daemon.tasks.verbs = true;
      s.daemon.tasks.seed({ id: 9401, from: ARCH, to: 'me', title: 'Keep or remove pc-chat?', state: 'doing', tags: ['worktree-removal'] });
      void s.daemon.inbox.add({ kind: 'decision', agent: ARCH, title: 'Keep or remove pc-chat?', body: 'Path: /work/pc-chat', task: 9401 });
    });
    await press($('nav-inbox'));
    await until(() => $('inbox-open-6'), 'the card');
    await press($('inbox-open-6'));
    await until(() => $('inbox-worktree-6-force'), 'the buttons');
    expect(text($('inbox-worktree-6-save'))).toBe('Save to a branch, then delete');
    expect(text($('inbox-worktree-6-force'))).toBe('Force delete');
    expect(text($('inbox-worktree-6-keep'))).toBe('Keep the worktree');
    expect(box.querySelector('[data-testid^="inbox-done-"]')).toBeNull();
    await press($('inbox-worktree-6-keep'));
    await until(() => m.daemon.tasks.get({ task: 9401 }).state === 'done', 'the daemon');
    expect(m.daemon.tasks.get({ task: 9401 }).evidence).toBe('keep');
    m.store.stop();
  }, 20000);
});

describe('the leftover worktrees under the disk card', () => {
  const disk = [{ kind: 'disk' as const, since: '2026-10-30T08:00:00Z', text: 'Free space is 3 GB.' }];
  it('lists them, removes a clean one at once, and asks before one that holds work', async () => {
    const m = await mount(
      (s) => {
        s.daemon.admin.worktrees.rows.push(orphan('merged-one'), orphan('busy-one', { merged: false, files_changed: 4, commits_ahead: 1 }));
        void setMachineNeeds(s, disk);
      },
      undefined,
      'founder',
      { role: 'founder' },
    );
    await press($('nav-inbox'));
    await until(() => $('disk-open'), 'the card');
    await press($('disk-open'));
    await until(() => $('disk-orphans-open'), 'the list button');
    expect(text($('disk-card'))).toContain('Free space is 3 GB.');
    expect(text($('disk-orphans-open'))).toContain('Leftover worktrees: 2, 2.0 GB');
    await press($('disk-orphans-open'));
    await press($('orphan-remove-merged-one'));
    await until(() => !m.daemon.admin.worktrees.rows.some((w) => w.path === '/work/merged-one'), 'the removal');
    await until(() => $('orphan-remove-merged-one') === null, 'the row gone');
    await press($('orphan-remove-busy-one'));
    await until(() => $('orphan-force'), 'the question');
    expect(text($('orphan-ask-sheet'))).toContain('4 unsaved files, 1 unpushed commit');
    expect(m.daemon.admin.worktrees.rows.some((w) => w.path === '/work/busy-one')).toBe(true);
    await press($('orphan-force'));
    await until(() => !m.daemon.admin.worktrees.rows.some((w) => w.path === '/work/busy-one'), 'the forced removal');
    m.store.stop();
  }, 20000);

  it('shows the card but no list to a member phone', async () => {
    const m = await mount(
      (s) => {
        s.daemon.admin.worktrees.rows.push(orphan('merged-one'));
        void setMachineNeeds(s, disk);
      },
      undefined,
      'member',
      { role: 'member' },
    );
    await press($('nav-inbox'));
    await until(() => $('disk-open'), 'the card');
    await press($('disk-open'));
    expect(text($('disk-card'))).toContain('Free space is 3 GB.');
    expect($('disk-orphans-open')).toBeNull();
    m.store.stop();
  }, 20000);
});
