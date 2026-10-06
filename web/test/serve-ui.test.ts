import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import type { AddressInfo } from 'node:net';
import { handler, resolve } from '../serve-ui.ts';

async function get(path: string) {
  const server = http.createServer(handler);
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  try {
    const res = await fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}${path}`, { redirect: 'manual' });
    return { status: res.status, type: res.headers.get('content-type') ?? '', loc: res.headers.get('location'), text: await res.text() };
  } finally {
    server.close();
  }
}

test('the page, its styles and its scripts are served; scripts have their types stripped', async () => {
  assert.equal((await get('/')).loc, '/ui/');
  assert.match((await get('/ui/')).text, /<div id="app"/);
  assert.match((await get('/ui/style.css')).type, /text\/css/);
  const app = await get('/app-core/model.ts');
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

test('a malformed request line is answered, not fatal', async () => {
  const server = http.createServer(handler);
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  try {
    const port = (server.address() as AddressInfo).port;
    for (const line of ['GET http://[ HTTP/1.1', 'GET /%zz HTTP/1.1', 'GET //// HTTP/1.1']) {
      const reply = await new Promise<string>((ok) => {
        const s = net.connect(port, '127.0.0.1', () => s.write(`${line}\r\nHost: x\r\nConnection: close\r\n\r\n`));
        let got = '';
        s.on('data', (d) => {
          got += d;
        });
        s.on('close', () => ok(got));
        s.on('error', () => ok(got));
      });
      assert.match(reply, /^HTTP\/1\.1 (400|404)/, line);
    }
    assert.equal((await fetch(`http://127.0.0.1:${port}/ui/`)).status, 200); // still serving
  } finally {
    server.close();
  }
});

test('with --cert the UI is served over https, and a non-loopback host without one is refused', async (t) => {
  const dir = fs.mkdtempSync(path.join(process.env.TMPDIR ?? os.tmpdir(), 'tls-'));
  try {
    execFileSync(
      'openssl',
      [
        'req',
        '-x509',
        '-newkey',
        'rsa:2048',
        '-nodes',
        '-days',
        '1',
        '-subj',
        '/CN=localhost',
        '-keyout',
        `${dir}/k.pem`,
        '-out',
        `${dir}/c.pem`,
      ],
      { stdio: 'ignore' },
    );
  } catch {
    t.skip('openssl is not available');
    return;
  }
  const server = https.createServer({ cert: fs.readFileSync(`${dir}/c.pem`), key: fs.readFileSync(`${dir}/k.pem`) }, handler);
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  try {
    const body = await new Promise<string>((ok, bad) =>
      https
        .get({ host: '127.0.0.1', port: (server.address() as AddressInfo).port, path: '/ui/', rejectUnauthorized: false }, (r) => {
          let b = '';
          r.on('data', (d) => {
            b += d;
          });
          r.on('end', () => ok(b));
        })
        .on('error', bad),
    );
    assert.match(body, /<div id="app"/);
  } finally {
    server.close();
  }
  const r = spawnSync(process.execPath, [path.join(import.meta.dirname, '..', 'serve-ui.ts'), '--host', '0.0.0.0', '--port', '0'], {
    encoding: 'utf8',
    timeout: 10000,
  });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /needs --cert and --key/);
});
