import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { RpcError } from '@handline/client';
import { applySeeds } from '#src/seeds.ts';
import { setup } from '#src/helpers.ts';

test('accounts.list gives alpha ahead of its pace line, beta behind it and gamma over it with a back_at', async () => {
  const { client } = setup();
  await client.start();
  const by = Object.fromEntries((await client.call('accounts.list', { usage: true })).accounts.map((a) => [a.name, a.pace]));
  assert.equal(by.alpha?.state, 'ahead');
  assert.equal(by.beta?.state, 'behind');
  assert.equal(by.gamma?.state, 'over');
  assert.equal(by.alpha?.used_pct, 62);
  assert.ok(by.alpha!.used_pct > by.alpha!.line_pct && by.alpha!.used_pct - by.alpha!.line_pct <= 10);
  assert.ok(by.beta!.used_pct <= by.beta!.line_pct);
  assert.ok(by.gamma!.used_pct - by.gamma!.line_pct > 10);
  assert.equal(by.alpha?.back_at, null);
  assert.equal(by.beta?.back_at, null);
  assert.ok(Date.parse(by.gamma!.back_at!) > Date.now());
  client.close();
});

test('settings.get gives the Go pacing defaults and pace_target_pct reads back after settings.set', async () => {
  const { client } = setup();
  await client.start();
  const v = (await client.call('settings.get', {})).values;
  assert.deepEqual([v.pacing, v.pace_target_pct, v.pace_burst_pct], [true, 98, 10]);
  await client.call('settings.set', { changes: { pace_target_pct: 90 }, request_id: 'm1-a' });
  assert.equal((await client.call('settings.get', {})).values.pace_target_pct, 90);
  await client.call('settings.set', { changes: { pace_target_pct: null }, request_id: 'm1-b' });
  assert.equal((await client.call('settings.get', {})).values.pace_target_pct, 98);
  client.close();
});

test('the hr-held seed lists an applying proposal with waiting_on and one the founder asked for, and refuses to approve the applying one', async () => {
  const { client, daemon } = setup();
  applySeeds(daemon, 'hr-held');
  await client.start();
  const { proposals } = await client.call('hr.list', {});
  const applying = proposals.find((p) => p.state === 'applying')!;
  assert.deepEqual(applying.waiting_on, ['handline.auditor']);
  assert.equal(proposals.find((p) => p.requested_by === 'human')?.state, 'open');
  await assert.rejects(
    client.call('hr.decide', { proposal: applying.id, decision: 'approve', subject: applying.subject }),
    (e: RpcError) => e.gate === 'proposal_settled',
  );
  client.close();
});
