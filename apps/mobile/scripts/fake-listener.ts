// The fake daemon as the paired-device listener: wss with a fresh self-signed certificate, paired devices only.
// Prints the pairing uri for the phone. `node scripts/fake-listener.ts [--port 7788] [--host 127.0.0.1]`
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { FakeDaemon } from '@handline/fake-daemon/daemon';
import { serveWs } from '@handline/fake-daemon/serve';

const arg = (n: string, d: string) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1]! : d; };
const host = arg('host', '127.0.0.1'), port = Number(arg('port', '7788'));
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fake-listener-'));
execFileSync('openssl', ['req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1', '-nodes', '-days', '2', '-subj', '/CN=handline-fake',
  '-addext', `subjectAltName=IP:${host}`, '-keyout', `${dir}/k.pem`, '-out', `${dir}/c.pem`], { stdio: 'ignore' });
const cert = fs.readFileSync(`${dir}/c.pem`);
const daemon = new FakeDaemon();
daemon.autoplay(Number(arg('autoplay', '1500')));
serveWs(daemon, port, host, { cert, key: fs.readFileSync(`${dir}/k.pem`) }, true);
const fp = crypto.createHash('sha256').update(new crypto.X509Certificate(cert).raw).digest('hex');
const uri = `handline://pair?${new URLSearchParams({ host: `${host}:${port}`, fp, code: daemon.issueCode() })}`;
console.log(uri);
