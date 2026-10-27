import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { setup } from '#src/helpers.ts';

const KEYS = [
  'active_cap',
  'park_timeout_s',
  'handoff_note',
  'ignore_cache_expiry',
  'dev_mode',
  'bg_notice_s',
  'bg_grace_s',
  'bg_check_s',
  'notice_digest_s',
  'token_prices',
  'input_hold_idle_s',
  'handoff_prompt',
  'push_prompt_asked',
  'push_task_done',
  'push_stall',
  'push_account_out',
  'pacing',
  'pace_target_pct',
  'pace_burst_pct',
  'host_active_cap',
  'load_per_core_pct',
  'ram_free_min_pct',
  'host_simulator_hold',
  'heavy_slots',
  'keep_awake',
  'reap_orphans',
];

test('settings.get serves every SettingsValues key, a load limit reads back, and null restores the default', async () => {
  const { client } = setup();
  await client.start();
  const first = await client.call('settings.get', {});
  assert.deepEqual(Object.keys(first.values).sort(), [...KEYS].sort());
  assert.deepEqual([first.values.host_active_cap, first.values.load_per_core_pct, first.values.ram_free_min_pct], [0, 150, 10]);
  assert.equal(first.timings.bg_grace_s, 300);
  await client.call('settings.set', { changes: { load_per_core_pct: 200, push_stall: false }, request_id: 'l1-a' });
  const set = await client.call('settings.get', {});
  assert.deepEqual([set.values.load_per_core_pct, set.values.push_stall], [200, false]);
  await client.call('settings.set', { changes: { load_per_core_pct: null }, request_id: 'l1-b' });
  assert.equal((await client.call('settings.get', {})).values.load_per_core_pct, 150);
  assert.deepEqual([first.values.heavy_slots, first.values.keep_awake], [2, 'off']);
  await assert.rejects(
    client.call('settings.set', { changes: { keep_awake: 'always' as never }, request_id: 'l1-d' }),
    (e: RpcError) => e.code === 'bad_request',
  );
  await client.call('settings.set', { changes: { keep_awake: 'working', heavy_slots: 3 }, request_id: 'l1-e' });
  const awake = (await client.call('settings.get', {})).values;
  assert.deepEqual([awake.keep_awake, awake.heavy_slots], ['working', 3]);
  client.close();
});

test('connection.get answers a TLS listener with a fingerprint, one heavy slot and dev mode as set', async () => {
  const { client } = setup();
  await client.start();
  const got = await client.call('connection.get', {});
  assert.equal(got.served_by, 'daemon');
  assert.equal(got.listener?.encryption, 'tls');
  assert.match(got.listener?.fingerprint ?? '', /^sha256:[0-9a-f]{64}$/);
  assert.equal(got.heavy_slots.slots, 1);
  assert.equal(got.dev_mode, false);
  await client.call('settings.set', { changes: { dev_mode: true }, request_id: 'l1-c' });
  assert.equal((await client.call('connection.get', {})).dev_mode, true);
  client.close();
});

test('a member is refused connection.get, and a daemon without admin does not list it', async () => {
  const member = setup({ role: 'member' });
  await member.client.start();
  await assert.rejects(member.client.call('connection.get', {}), (e: RpcError) => e.gate === 'role');
  member.client.close();
  const none = setup({ daemon: { admin: false } });
  await none.client.start();
  assert.equal(
    none.client.hello?.methods.some((m) => m.name === 'connection.get'),
    false,
  );
  none.client.close();
});

test('settings.get shows the built-in handoff prompt and who sets their own; a short prompt is refused and line breaks are joined', async () => {
  const { client } = setup();
  await client.start();
  const got = await client.call('settings.get', {});
  assert.equal(got.handoff_prompt_default, got.values.handoff_prompt);
  assert.deepEqual(got.handoff_prompt_own, []);
  await assert.rejects(
    client.call('settings.set', { changes: { handoff_prompt: ' too short ' }, request_id: 'h1-a' }),
    (e: RpcError) => e.code === 'bad_request',
  );
  await client.call('settings.set', { changes: { handoff_prompt: 'Write {path}:\n what is next,\r\n and why.' }, request_id: 'h1-b' });
  assert.equal((await client.call('settings.get', {})).values.handoff_prompt, 'Write {path}: what is next, and why.');
  client.close();
});
