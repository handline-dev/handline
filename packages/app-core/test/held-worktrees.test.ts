import { test } from 'node:test';
import assert from 'node:assert/strict';
import { heldFromData, heldWords } from '#src/index.ts';

test('the work a worktree holds is said in plain words, singular and plural', () => {
  assert.equal(heldWords([{ path: '/a', files_changed: 3, commits_ahead: 2 }]), '3 unsaved files, 2 unpushed commits');
  assert.equal(heldWords([{ path: '/a', files_changed: 1, commits_ahead: 0 }]), '1 unsaved file');
  assert.equal(
    heldWords([
      { path: '/a', files_changed: 0, commits_ahead: 1 },
      { path: '/b', files_changed: 2, commits_ahead: 0 },
    ]),
    '2 unsaved files, 1 unpushed commit',
  );
});

test('the dirty refusal data is read, and a shape it does not know is ignored', () => {
  assert.deepEqual(heldFromData({ worktrees: [{ path: '/a', files_changed: 2, commits_ahead: 1 }, { nope: 1 }] }), [
    { path: '/a', files_changed: 2, commits_ahead: 1 },
  ]);
  assert.deepEqual(heldFromData(undefined), []);
  assert.deepEqual(heldFromData({ worktrees: 'x' }), []);
});
