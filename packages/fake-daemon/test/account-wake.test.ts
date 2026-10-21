import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type Event, type RpcError } from '@handline/client';
import { setup } from '#src/helpers.ts';

const stalledOf = (daemon: ReturnType<typeof setup>['daemon'], name: string) =>
  daemon.accounts.list().accounts.find((a) => a.name === name)?.stalled;

test('a stalled account is listed with its reason, and account.wake ends it and sends the row', async () => {
  const { client, daemon } = setup();
  daemon.accounts.stall('beta', { reason: 'login expired', error: 'authentication_failed' });
  await client.start();
  const seen: Event[] = [];
  client.onEvent((e) => e.topic === 'accounts' && seen.push(e));
  await client.subscribe(['accounts']);
  assert.equal(stalledOf(daemon, 'beta')?.reason, 'login expired');
  assert.equal(stalledOf(daemon, 'alpha'), undefined);
  await client.call('account.wake', { account: 'beta', request_id: 'w1' });
  assert.equal(stalledOf(daemon, 'beta'), null);
  assert.equal((seen.at(-1)!.data as { stalled: unknown }).stalled, null);
  client.close();
});

test('waking an account that is not stalled is refused (state), an unknown one is not_found', async () => {
  const { client } = setup();
  await client.start();
  const refused = (await client.call('account.wake', { account: 'alpha', request_id: 'w2' }).catch((e: RpcError) => e)) as RpcError;
  assert.equal((refused.data as { gate?: string }).gate, 'state');
  const missing = (await client.call('account.wake', { account: 'nope', request_id: 'w3' }).catch((e: RpcError) => e)) as RpcError;
  assert.equal((missing.data as { code?: string }).code, 'not_found');
  client.close();
});
