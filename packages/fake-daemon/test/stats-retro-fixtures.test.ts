// contract/fixtures/stats_retro.json against the fake over a Unix socket, through the conformance harness every daemon runs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { FakeDaemon } from '#src/daemon.ts';
import { serveSocket } from '#src/serve.ts';

const ROOT = join(import.meta.dirname, '..', '..', '..');

test('every case of stats_retro.json passes on the fake', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'fk-'));
  const sock = join(dir, 's');
  const server = serveSocket(new FakeDaemon(), sock);
  try {
    await once(server, 'listening');
    const run = spawn(
      'python3',
      [
        join(ROOT, 'contract/conformance.py'),
        sock,
        '--var',
        'founder_key=x',
        '--var',
        'retro_task=9001',
        '--var',
        'retro_plain_task=9003',
        join(ROOT, 'contract/fixtures/stats_retro.json'),
      ],
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
