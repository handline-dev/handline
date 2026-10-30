// contract/fixtures/attachment_remove.json against the fake over a Unix socket, through the conformance harness every daemon runs.
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

test('every case of attachment_remove.json passes on the fake', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'fk-'));
  const sock = join(dir, 's');
  const daemon = new FakeDaemon();
  daemon.tasks.verbs = true; // the image is named in a task comment
  const server = serveSocket(daemon, sock);
  try {
    await once(server, 'listening');
    // The agent case is skipped here, and --allow-skip says that is meant (exit 3 otherwise); the Go run plays it.
    const run = spawn(
      'python3',
      [
        join(ROOT, 'contract/conformance.py'),
        sock,
        '--allow-skip',
        '--var',
        'founder_key=x',
        '--var',
        'ask_agent=handline.auditor',
        join(ROOT, 'contract/fixtures/attachment_remove.json'),
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
