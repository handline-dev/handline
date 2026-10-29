import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from '#src/helpers.ts';

test('stats.get serves a use row per agent and account, and an account rows add up to its rise', async () => {
  const { client } = setup();
  await client.start();
  for (const window of ['24h', '7d'] as const) {
    const stats = await client.call('stats.get', { window });
    assert.ok(!('working_seconds' in stats));
    const costs = stats.use.map((u) => u.cost);
    assert.deepEqual(
      costs,
      [...costs].sort((a, b) => b - a),
      'largest cost first',
    );
    const sum = (account: string) => stats.use.filter((u) => u.account === account).reduce((n, u) => n + (u.week_pct ?? 0), 0);
    const rise = window === '7d' ? { alpha: 30, beta: 10 } : { alpha: 8, beta: 3 };
    assert.ok(Math.abs(sum('alpha') - rise.alpha) < 0.02 && Math.abs(sum('beta') - rise.beta) < 0.02);
    assert.ok(
      stats.use.some((u) => u.agent === ''),
      'calls of no agent are agent ""',
    );
    assert.equal(stats.use.find((u) => u.account === 'gamma')?.week_pct, null);
    assert.ok(
      new Set(stats.use.filter((u) => u.account === 'alpha' || u.account === 'beta').map((u) => u.agent)).size < stats.use.length,
      'an agent on two accounts has two rows',
    );
  }
  client.close();
});
