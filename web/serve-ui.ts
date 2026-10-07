// Serves the web UI with no build step: TypeScript is type-stripped per request, so the browser loads the same
// .ts files the tests run. With --fake it also runs the fake daemon (a WebSocket on --ws), otherwise point the page
// at a daemon with ?daemon=wss://host:port.
//   node serve-ui.ts [--cert FILE --key FILE] [--port 8080] [--host 127.0.0.1] [--fake] [--ws 7777] [--autoplay MS]
// TLS is the rule: with --cert the UI is https (and the fake daemon wss); without it only loopback is served, plain, for development.
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import { stripTypeScriptTypes } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const WEB = path.dirname(fileURLToPath(import.meta.url));
const ROOTS: Record<string, string> = {
  '/ui/': path.join(WEB, 'ui'),
  '/legacy/': path.join(WEB, 'legacy'),
  '/src/': path.join(WEB, '..', 'packages', 'client', 'src'),
  '/app-core/': path.join(WEB, '..', 'packages', 'app-core', 'src'),
  '/tokens/': path.join(WEB, '..', 'packages', 'tokens', 'src'),
  '/contract/': path.join(WEB, '..', 'contract'),
};
const TYPES: Record<string, string> = {
  '.html': 'text/html',
  '.css': 'text/css',
  '.ts': 'text/javascript',
  '.js': 'text/javascript',
  '.svg': 'image/svg+xml',
};

/** The file a URL path names, only under a served root; undefined for anything else. */
export function resolve(urlPath: string): string | undefined {
  const clean = path.posix.normalize(decodeURIComponent(urlPath));
  for (const [prefix, dir] of Object.entries(ROOTS)) {
    if (!clean.startsWith(prefix)) continue;
    const file = path.join(dir, clean.slice(prefix.length));
    if (file.startsWith(dir + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile()) return file;
  }
  return undefined;
}

export function handler(req: http.IncomingMessage, res: http.ServerResponse): void {
  try {
    let url: URL;
    try {
      url = new URL(req.url ?? '/', 'http://x');
    } catch {
      res.writeHead(400, { 'content-type': 'text/plain' });
      res.end('bad request');
      return;
    }
    if (url.pathname === '/') {
      res.writeHead(302, { location: `/ui/${url.search}` });
      res.end();
      return;
    }
    const target = url.pathname.endsWith('/') ? url.pathname + 'index.html' : url.pathname;
    let file: string | undefined;
    try {
      file = resolve(target);
    } catch {
      file = undefined;
    } // a bad percent-escape names nothing
    if (!file) {
      res.writeHead(404, { 'content-type': 'text/plain' });
      res.end('not found');
      return;
    }
    const ext = path.extname(file);
    const body = ext === '.ts' ? stripTypeScriptTypes(fs.readFileSync(file, 'utf8')) : fs.readFileSync(file);
    res.writeHead(200, { 'content-type': `${TYPES[ext] ?? 'application/octet-stream'}; charset=utf-8`, 'cache-control': 'no-store' });
    res.end(body);
  } catch (e) {
    console.error(e);
    if (!res.headersSent) res.writeHead(500, { 'content-type': 'text/plain' });
    res.end('server error');
  }
}

if (import.meta.url === pathToFileURL(process.argv[1]!).href) {
  const arg = (n: string) => {
    const i = process.argv.indexOf(`--${n}`);
    return i > 0 ? process.argv[i + 1] : undefined;
  };
  const cert = arg('cert'),
    key = arg('key');
  if (!!cert !== !!key) {
    console.error('--cert and --key go together');
    process.exit(2);
  }
  const host = arg('host') ?? (cert ? '0.0.0.0' : '127.0.0.1'),
    port = Number(arg('port') ?? (cert ? 8443 : 8080));
  if (!cert && !['127.0.0.1', 'localhost', '::1'].includes(host)) {
    console.error('Serving beyond this machine needs --cert and --key.');
    process.exit(2);
  }
  const tls = cert ? { cert: fs.readFileSync(cert), key: fs.readFileSync(key!) } : undefined;
  if (process.argv.includes('--fake')) {
    const { FakeDaemon } = await import('@handline/fake-daemon/daemon');
    const { serveWs } = await import('@handline/fake-daemon/serve');
    const daemon = new FakeDaemon();
    const ms = Number(arg('autoplay') ?? '1000');
    if (ms > 0) daemon.autoplay(ms);
    const ws = Number(arg('ws') ?? '7777');
    serveWs(daemon, ws, host, tls);
    console.log(`fake daemon: ${tls ? 'wss' : 'ws'}://${host}:${ws}`);
  }
  const server = tls ? https.createServer(tls, handler) : http.createServer(handler);
  server.listen(port, host, () => console.log(`web UI: ${tls ? 'https' : 'http'}://${host}:${port}/`));
}
