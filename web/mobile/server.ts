// Serves the mobile app to a phone on the LAN or Tailscale: static files, with TypeScript stripped per request
// (Node's own type stripping, the way the tests run it), so there is no build step and no dependencies.
//   node mobile/server.ts [--port 8080] [--host 0.0.0.0]
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { stripTypeScriptTypes } from 'node:module';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const TYPES: Record<string, string> = {
  '.html': 'text/html', '.css': 'text/css', '.webmanifest': 'application/manifest+json', '.js': 'text/javascript', '.ts': 'text/javascript',
};
// Only what the page imports: the app, the shared client, and the contract's generated types.
const ALLOWED = [/^\/web\/mobile\/[\w.-]+$/, /^\/web\/src\/[\w.-]+\.ts$/, /^\/contract\/protocol\.ts$/];

export function handler(req: http.IncomingMessage, res: http.ServerResponse): void {
  let url = decodeURIComponent((req.url ?? '/').split('?')[0]!);
  if (url === '/') url = '/web/mobile/index.html';
  const file = path.join(REPO, url);
  if (!ALLOWED.some((re) => re.test(url)) || !file.startsWith(REPO) || /server\.ts$|\.test\.ts$/.test(url) || !fs.existsSync(file)) {
    res.writeHead(404).end('not found'); return;
  }
  const ext = path.extname(file);
  let body: string | Buffer = fs.readFileSync(file);
  if (ext === '.ts') body = stripTypeScriptTypes(body.toString());
  res.writeHead(200, { 'content-type': `${TYPES[ext] ?? 'application/octet-stream'}; charset=utf-8`, 'cache-control': 'no-cache' }).end(body);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const arg = (n: string, d: string) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1]! : d; };
  const host = arg('host', '0.0.0.0'), port = Number(arg('port', '8080'));
  http.createServer(handler).listen(port, host, () => console.log(`mobile app on http://${host}:${port}/  (daemon: ?daemon=ws://<host>:7777)`));
}
