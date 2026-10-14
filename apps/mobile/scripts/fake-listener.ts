// The fake daemon as the paired-device listener: wss with a fresh self-signed certificate, paired devices only.
// Prints the pairing uri for the phone. `node scripts/fake-listener.ts [--port 7788] [--host 127.0.0.1] [--latency ms] [--refuse method:gate:reason] [--founder-phone] [--prompt]`
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { FakeDaemon } from '@handline/fake-daemon/daemon';
import { serveWs } from '@handline/fake-daemon/serve';

const arg = (n: string, d: string) => {
  const i = process.argv.indexOf(`--${n}`);
  return i > 0 ? process.argv[i + 1]! : d;
};
const host = arg('host', '127.0.0.1'),
  port = Number(arg('port', '7788'));
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fake-listener-'));
execFileSync(
  'openssl',
  [
    'req',
    '-x509',
    '-newkey',
    'ec',
    '-pkeyopt',
    'ec_paramgen_curve:prime256v1',
    '-nodes',
    '-days',
    '2',
    '-subj',
    '/CN=handline-fake',
    '-addext',
    `subjectAltName=IP:${host}`,
    '-keyout',
    `${dir}/k.pem`,
    '-out',
    `${dir}/c.pem`,
  ],
  { stdio: 'ignore' },
);
const cert = fs.readFileSync(`${dir}/c.pem`);
const daemon = new FakeDaemon();
daemon.tasks.verbs = true; // done, reopen, queue... (the daemon's plan D3), so the phone's task verbs can be tried
if (process.argv.includes('--founder-phone')) daemon.deviceRole = 'founder'; // so the HR verbs can be tried: a real phone is a member
if (process.argv.includes('--prompt')) {
  // The phone cannot type into a terminal, so a permission that waits on handline.architect is put in here, as the tests do.
  const s = daemon.sessions.get('handline.architect')!;
  s.state = 'waiting';
  s.prompt = {
    id: 'p-e2e',
    agent: s.fx.name,
    kind: 'permission',
    tool: 'Bash',
    command: 'git push origin main',
    reason: null,
    lines: [],
    since: new Date().toISOString(),
    options: [
      { key: '1', label: 'Yes', answer: 'yes', highlighted: true },
      { key: '2', label: 'No', answer: 'no' },
    ],
  };
}
if (process.argv.includes('--picture')) {
  // The phone cannot attach, so a message with one image is put in handline.architect's chat, as the tests do.
  const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
  const id = daemon.uploads.put({ name: 'dot.png', mime: 'image/png', bytes: 70, data: png }).attachment!.id;
  daemon.chat.send({ agent: 'handline.architect', body: 'Here is a picture', request_id: 'e2e-picture', attachments: [id] }, 'founder', daemon.uploads.take);
}
daemon.raiseProposal({
  id: 1,
  title: 'settings handline.architect',
  agent: 'handline.architect',
  diff: '- effort  high\n+ effort  max',
  reason: 'it needs more thought',
});
daemon.raiseProposal({
  id: 2,
  title: 'rename handline.auditor',
  agent: 'handline.auditor',
  diff: '- handline.auditor\n+ handline.reviewer',
  reason: 'the role changed',
});
daemon.autoplay(Number(arg('autoplay', '1500')));
// For watching the loading and refusal states on a device: `--latency 800` and `--refuse agents.list:state:the daemon is resting`
// (method:gate:reason; repeat the flag for more). Both apply to calls after the pairing hello.
daemon.knobs.latency(Number(arg('latency', '0')));
process.argv.forEach((a, i) => {
  if (a !== '--refuse') return;
  const [method = '', gate = 'state', ...reason] = process.argv[i + 1]!.split(':');
  daemon.knobs.refuse(method, gate, reason.join(':') || 'refused');
});
serveWs(daemon, port, host, { cert, key: fs.readFileSync(`${dir}/k.pem`) }, true);
const fp = crypto.createHash('sha256').update(new crypto.X509Certificate(cert).raw).digest('hex');
const uri = `handline://pair?${new URLSearchParams({ host: `${host}:${port}`, fp, code: daemon.issueCode() })}`;
console.log(uri);
