import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { handler, resolve } from '../serve-ui.ts';

async function get(path: string) {
  const server = http.createServer(handler);
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  try {
    const res = await fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}${path}`, { redirect: 'manual' });
    return { status: res.status, type: res.headers.get('content-type') ?? '', loc: res.headers.get('location'), text: await res.text() };
  } finally { server.close(); }
}

test('the page, its styles and its scripts are served; scripts have their types stripped', async () => {
  assert.equal((await get('/')).loc, '/ui/');
  assert.match((await get('/ui/')).text, /<div id="app"/);
  assert.match((await get('/ui/style.css')).type, /text\/css/);
  const app = await get('/ui/model.ts');
  assert.match(app.type, /text\/javascript/);
  assert.doesNotMatch(app.text, /: AgentView\b/);
  assert.match((await get('/src/index.ts')).text, /export \*/);
  assert.match((await get('/contract/protocol.ts')).text, /PROTOCOL/);
});

test('nothing outside the served folders is reachable', async () => {
  assert.equal((await get('/ui/../package.json')).status, 404);
  assert.equal((await get('/ui/%2e%2e/package.json')).status, 404);
  assert.equal((await get('/etc/passwd')).status, 404);
  assert.equal(resolve('/ui/%2e%2e%2fpackage.json'), undefined);
  assert.equal((await get('/nope.ts')).status, 404);
});
