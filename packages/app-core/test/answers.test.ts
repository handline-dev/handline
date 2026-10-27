import assert from 'node:assert/strict';
import { test } from 'node:test';
import { answerLine, limitFields } from '#src/index.ts';

test('a standing answer reads as a sentence, not as its key', () => {
  assert.equal(answerLine({ kind: 'trust', key: ['/work/a'], text: '/work/a' }), 'Trust the folder /work/a when an agent asks');
  assert.equal(
    answerLine({
      kind: 'dialog',
      key: ['claude', 'Do you trust the files', 'Yes, I trust'],
      text: 'claude: Do you trust the files → Yes, I trust',
    }),
    'When claude asks "Do you trust the files", choose "Yes, I trust"',
  );
  assert.equal(answerLine({ kind: 'dialog', key: ['x'], text: 'x: raw' }), 'x: raw');
});

test('a limit of 0 reads "no limit" with no unit beside it', () => {
  const [cap] = limitFields({ host_active_cap: 0, load_per_core_pct: 150, ram_free_min_pct: 10 });
  assert.equal(cap!.current, 'no limit');
  assert.equal(cap!.unit, '');
  assert.equal(limitFields({ host_active_cap: 4 })[0]!.unit, 'agents');
});
