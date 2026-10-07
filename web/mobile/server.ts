// Serves the mobile app to a phone on the LAN or Tailscale: static files, with TypeScript stripped per request
// (Node's own type stripping, the way the tests run it), so there is no build step and no dependencies.
//   node mobile/server.ts --cert FILE --key FILE [--port 8443] [--host 0.0.0.0]
// TLS is the rule: a phone only installs and trusts a secure page. Without --cert, it serves plain http on localhost for development.
import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import path from 'node:path';
import { stripTypeScriptTypes } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const TYPES: Record<string, string> = {
  '.html': 'text/html',
  '.css': 'text/css',
  '.webmanifest': 'application/manifest+json',
  '.js': 'text/javascript',
  '.ts': 'text/javascript',
  '.png': 'image/png',
};
// Only what the page imports: the app, the shared client (@handline/client, served from /packages/client/src, mapped in index.html), and the contract's generated types.
const ALLOWED = [
  /^\/web\/mobile\/(?!\.+$)[\w.-]+$/,
  /^\/web\/legacy\/store\.ts$/,
  /^\/packages\/(client|app-core|tokens)\/src\/[\w.-]+\.ts$/,
  /^\/contract\/protocol\.ts$/,
];

export function handler(req: http.IncomingMessage, res: http.ServerResponse): void {
  try {
    let url: string;
    try {
      url = decodeURIComponent((req.url ?? '/').split('?')[0]!);
    } catch {
      res.writeHead(400).end('bad request');
      return;
    }
    if (url === '/') url = '/web/mobile/index.html';
    const file = path.join(REPO, url);
    const served =
      ALLOWED.some((re) => re.test(url)) && !/server\.ts$|\.test\.ts$/.test(url) && fs.statSync(file, { throwIfNoEntry: false })?.isFile();
    if (!served) {
      res.writeHead(404).end('not found');
      return;
    }
    const ext = path.extname(file);
    let body: string | Buffer = fs.readFileSync(file);
    if (ext === '.ts') body = stripTypeScriptTypes(body.toString());
    res
      .writeHead(200, {
        'content-type': `${TYPES[ext] ?? 'application/octet-stream'}${ext === '.png' ? '' : '; charset=utf-8'}`,
        'cache-control': 'no-cache',
      })
      .end(body);
  } catch (e) {
    console.error(e);
    if (!res.headersSent) res.writeHead(500);
    res.end('server error');
  }
}

const LOOPBACK = new Set(['127.0.0.1', '::1', 'localhost']);

/** Where and how to listen. Plain http may only be reached from this machine; anything wider needs TLS. */
export function listenOptions(argv: string[]): { host: string; port: number; cert?: string; key?: string } {
  const arg = (n: string) => {
    const i = argv.indexOf(`--${n}`);
    return i >= 0 ? argv[i + 1] : undefined;
  };
  const cert = arg('cert'),
    key = arg('key');
  if (!!cert !== !!key) throw new Error('--cert and --key go together');
  const host = arg('host') ?? (cert ? '0.0.0.0' : '127.0.0.1');
  if (!cert && !LOOPBACK.has(host)) throw new Error(`refusing plain http on ${host}: give --cert and --key, or use a loopback host`);
  return { host, port: Number(arg('port') ?? (cert ? 8443 : 8080)), cert, key };
}

if (import.meta.url === pathToFileURL(process.argv[1]!).href) {
  let o;
  try {
    o = listenOptions(process.argv);
  } catch (e) {
    console.error((e as Error).message);
    process.exit(2);
  }
  const server = o.cert
    ? https.createServer({ cert: fs.readFileSync(o.cert), key: fs.readFileSync(o.key!) }, handler)
    : http.createServer(handler);
  server.listen(o.port, o.host, () => console.log(`mobile app on ${o.cert ? 'https' : 'http'}://${o.host}:${o.port}/`));
}
