import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from '../src/helpers.ts';

const ARCH = 'handline.architect';
const PATH = 'docs/pc-app-gaps.md';

test('file.read gives the text with an etag, and file.write takes it back and gives the next', async () => {
  const { client } = setup();
  await client.start();
  const r = await client.call('file.read', { agent: ARCH, path: PATH });
  assert.match(r.text, /^# PC app gaps/);
  assert.equal(r.truncated, false);
  const w = await client.call('file.write', { agent: ARCH, path: PATH, text: 'new', etag: r.etag, request_id: 'op-w1' });
  assert.notEqual(w.etag, r.etag);
  assert.equal((await client.call('file.read', { agent: ARCH, path: PATH })).text, 'new');
  await assert.rejects(client.call('file.write', { agent: ARCH, path: PATH, text: 'x', etag: r.etag, request_id: 'op-w2' }), /changed since that etag/);
  client.close();
});

test('file.write makes a new file only with an empty etag, and refuses what the daemon never writes', async () => {
  const { client } = setup();
  await client.start();
  await client.call('file.write', { agent: ARCH, path: 'docs/new.json', text: '{}', etag: '', request_id: 'op-n1' });
  await assert.rejects(client.call('file.write', { agent: ARCH, path: 'docs/new.json', text: '{}', etag: '', request_id: 'op-n2' }), /changed since/);
  for (const path of ['.claude/settings.json', '.github/x.md', 'package.json', 'src/main.go', 'hooks/a.md'])
    await assert.rejects(client.call('file.write', { agent: ARCH, path, text: 'x', etag: '', request_id: `op-${path}` }), /not a file the daemon writes/);
  for (const path of ['../x.md', '/etc/passwd', 'a//b.md'])
    await assert.rejects(client.call('file.read', { agent: ARCH, path }), /relative to the agent worktree/);
  client.close();
});
