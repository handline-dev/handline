import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type Event, type RpcError } from '@handline/client';
import { setup } from '../src/helpers.ts';
import { GOOD_LOGIN_CODE } from '../src/accounts.ts';

type Login = { login_id: string; state: string; url: string };

test('a login answers its id and URL, the code ends it done and the account is logged in', async () => {
  const { client, daemon } = setup();
  await client.start();
  const seen: Event[] = [];
  client.onEvent((e) => e.topic === 'accounts' && seen.push(e));
  await client.subscribe(['accounts']);
  const r = (await client.call('account.login', { account: 'gamma', request_id: 'l1' })) as unknown as Login;
  assert.equal(r.state, 'waiting');
  assert.match(r.url, /^https:/);
  assert.equal(
    ((await client.call('accounts.list', {})).accounts as { name: string; login: { state: string } | null }[]).find(
      (a) => a.name === 'gamma',
    )?.login?.state,
    'waiting',
  );
  await client.call('account.login_code', { login_id: r.login_id, code: GOOD_LOGIN_CODE, request_id: 'l2' });
  const states = seen.map((e) => (e.data as { login: { state: string } }).login.state);
  assert.deepEqual(states, ['waiting', 'code_sent', 'done']);
  assert.equal(daemon.accounts.list().accounts.find((a) => a.name === 'gamma')?.login_state, 'ok');
  client.close();
});

test('a wrong code ends the login failed with a reason; a second login is refused while one waits; cancel is idempotent', async () => {
  const { client, daemon } = setup();
  await client.start();
  const first = (await client.call('account.login', { account: 'gamma', request_id: 'm1' })) as unknown as Login;
  const again = await client.call('account.login', { account: 'gamma', request_id: 'm2' }).catch((e: RpcError) => e);
  assert.equal((again as RpcError).data?.gate, 'state');
  await client.call('account.login_code', { login_id: first.login_id, code: 'nope', request_id: 'm3' });
  const row = () => daemon.accounts.list().accounts.find((a) => a.name === 'gamma')!;
  assert.deepEqual([row().login?.state, row().login_state], ['failed', 'needs_login']);
  assert.equal(typeof row().login?.reason, 'string');
  const late = await client
    .call('account.login_code', { login_id: first.login_id, code: GOOD_LOGIN_CODE, request_id: 'm4' })
    .catch((e: RpcError) => e);
  assert.equal((late as RpcError).data?.gate, 'state');
  const second = (await client.call('account.login', { account: 'gamma', request_id: 'm5' })) as unknown as Login;
  await client.call('account.login_cancel', { login_id: second.login_id, request_id: 'm6' });
  await client.call('account.login_cancel', { login_id: second.login_id, request_id: 'm7' });
  assert.equal(row().login?.state, 'cancelled');
  client.close();
});

test('account.add registers an account that needs a login, and refuses a used name, a bad name and an unknown vendor', async () => {
  const { client, daemon } = setup();
  await client.start();
  const r = await client.call('account.add', { account: 'delta', request_id: 'a1' });
  assert.equal(r.account, 'delta');
  assert.equal(daemon.accounts.list().accounts.find((a) => a.name === 'delta')?.login_state, 'needs_login');
  const used = await client.call('account.add', { account: 'delta', request_id: 'a2' }).catch((e: RpcError) => e);
  assert.equal((used as RpcError).data?.gate, 'state');
  for (const [params, field] of [
    [{ account: 'a/b' }, 'account'],
    [{ account: '.x' }, 'account'],
    [{ account: 'epsilon', vendor: 'nosuch' }, 'vendor'],
  ] as const) {
    const e = (await client.call('account.add', { ...params, request_id: `b-${field}` }).catch((x: RpcError) => x)) as RpcError;
    assert.deepEqual([e.code, e.data?.field], ['bad_request', field]);
  }
  assert.equal(
    daemon.accounts.list().accounts.some((a) => a.name === 'epsilon'),
    false,
  );
  client.close();
});

test('a login code is kept in no request record: the same request_id is judged again, not replayed', async () => {
  const { client } = setup();
  await client.start();
  const login = (await client.call('account.login', { account: 'gamma', request_id: 'k1' })) as unknown as Login;
  const first = await client.call('account.login_code', { login_id: login.login_id, code: GOOD_LOGIN_CODE, request_id: 'k2' });
  assert.equal(first.replayed, undefined);
  const again = (await client
    .call('account.login_code', { login_id: login.login_id, code: GOOD_LOGIN_CODE, request_id: 'k2' })
    .catch((e: RpcError) => e)) as RpcError;
  assert.equal(again.data?.gate, 'state');
  client.close();
});
