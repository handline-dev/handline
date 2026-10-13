// contract/fixtures/chat.json against the fake over a Unix socket, through the conformance harness every daemon runs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { FakeDaemon } from '../src/daemon.ts';
import { serveSocket } from '../src/serve.ts';

const ROOT = join(import.meta.dirname, '..', '..', '..');

test('every case of chat.json passes on the fake', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'fk-'));
  const sock = join(dir, 's');
  const server = serveSocket(new FakeDaemon(), sock);
  try {
    await once(server, 'listening');
    // A case that needs an agent session is skipped, and --allow-skip says that is meant (exit 3 otherwise).
    const run = spawn(
      'python3',
      [join(ROOT, 'contract/conformance.py'), sock, '--allow-skip', '--var', 'founder_key=x', join(ROOT, 'contract/fixtures/chat.json')],
      { stdio: ['ignore', 'pipe', 'pipe'] },
    );
    let out = '';
    run.stdout.on('data', (d) => (out += d));
    run.stderr.on('data', (d) => (out += d));
    const [code] = await once(run, 'close');
    assert.equal(code, 0, out);
  } finally {
    server.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
