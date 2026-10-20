import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Outlook, Worktree } from '@handline/client';
import {
  costByAgent,
  ellipsis,
  fieldUnit,
  loginRunning,
  oneLine,
  searchSnippet,
  timeLeft,
  toolInputLine,
  worktreeSums,
  xtermColor,
} from '#src/index.ts';

test('text is cut with an ellipsis, folded to one line, and a tool input reads as its first string', () => {
  assert.equal(ellipsis('abcdef', 3), 'abc…');
  assert.equal(ellipsis('abc', 3), 'abc');
  assert.equal(oneLine('  a\n\tb  c ', 4), 'a b ');
  assert.equal(toolInputLine({ n: 1, path: 'src/x.ts' }, 80), 'src/x.ts');
  assert.equal(toolInputLine({ n: 1 }, 80), '{"n":1}');
});

test('a search snippet keeps 30 characters before the match and marks cut ends', () => {
  const body = `${'x'.repeat(40)} needle ${'y'.repeat(100)}`;
  const s = searchSnippet(body, 'NEEDLE');
  assert.ok(s.startsWith('…') && s.endsWith('…'));
  assert.equal(s.indexOf('needle'), 31);
  assert.equal(searchSnippet('short needle', 'needle'), 'short needle');
});

test('the xterm cube and greys', () => {
  assert.equal(xtermColor(16), '#000000');
  assert.equal(xtermColor(196), '#ff0000');
  assert.equal(xtermColor(231), '#ffffff');
  assert.equal(xtermColor(232), '#080808');
  assert.equal(xtermColor(255), '#eeeeee');
});

test('worktree sums, cost by agent, a running login, a time left and a field unit', () => {
  const w = {
    changed: [
      { added: 2, deleted: 1 },
      { added: 3, deleted: 0 },
    ],
  } as Worktree;
  assert.deepEqual(worktreeSums(w), { added: 5, deleted: 1 });
  const o = {
    cost_per_day: [
      { day: 'd1', agents: { a: 1, b: 2 } },
      { day: 'd2', agents: { a: 3 } },
    ],
  } as unknown as Outlook;
  assert.deepEqual(costByAgent(o), { agents: ['a', 'b'], sums: [4, 2], all: 6 });
  assert.equal(costByAgent({ cost_per_day: [] } as unknown as Outlook).all, 1);
  assert.equal(loginRunning({ login: { state: 'code_sent' } } as never), true);
  assert.equal(loginRunning({} as never), false);
  assert.equal(timeLeft('3h'), '3h left');
  assert.equal(timeLeft('over 2h'), 'over 2h');
  assert.equal(fieldUnit({ unit: 'min, 0 = never' } as never), 'min');
});
