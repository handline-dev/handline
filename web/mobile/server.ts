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
  '.html': 'text/html', '.css': 'text/css', '.webmanifest': 'application/manifest+json', '.js': 'text/javascript', '.ts': 'text/javascript', '.png': 'image/png',
};
// Only what the page imports: the app, the shared client, and the contract's generated types.
const ALLOWED = [/^\/web\/mobile\/(?!\.+$)[\w.-]+$/, /^\/web\/src\/[\w.-]+\.ts$/, /^\/contract\/protocol\.ts$/];

export function handler(req: http.IncomingMessage, res: http.ServerResponse): void {
  try {
    let url: string;
    try { url = decodeURIComponent((req.url ?? '/').split('?')[0]!); } catch { res.writeHead(400).end('bad request'); return; }
    if (url === '/') url = '/web/mobile/index.html';
    const file = path.join(REPO, url);
    const served = ALLOWED.some((re) => re.test(url)) && !/server\.ts$|\.test\.ts$/.test(url) && fs.statSync(file, { throwIfNoEntry: false })?.isFile();
    if (!served) { res.writeHead(404).end('not found'); return; }
    const ext = path.extname(file);
    let body: string | Buffer = fs.readFileSync(file);
    if (ext === '.ts') body = stripTypeScriptTypes(body.toString());
    res.writeHead(200, { 'content-type': `${TYPES[ext] ?? 'application/octet-stream'}${ext === '.png' ? '' : '; charset=utf-8'}`, 'cache-control': 'no-cache' }).end(body);
  } catch (e) {
    console.error(e);
    if (!res.headersSent) res.writeHead(500);
    res.end('server error');
  }
}

if (import.meta.url === pathToFileURL(process.argv[1]!).href) {
  const arg = (n: string) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1] : undefined; };
  const cert = arg('cert'), key = arg('key');
  if (!!cert !== !!key) { console.error('--cert and --key go together'); process.exit(2); }
  const host = arg('host') ?? (cert ? '0.0.0.0' : '127.0.0.1'), port = Number(arg('port') ?? (cert ? 8443 : 8080));
  const server = cert ? https.createServer({ cert: fs.readFileSync(cert), key: fs.readFileSync(key!) }, handler) : http.createServer(handler);
  server.listen(port, host, () => console.log(`mobile app on ${cert ? 'https' : 'http'}://${host}:${port}/`));
}
