// Serves the web UI with no build step: TypeScript is type-stripped per request, so the browser loads the same
// .ts files the tests run. With --fake it also runs the fake daemon (a WebSocket on --ws), otherwise point the page
// at a daemon with ?daemon=ws://host:port.
//   node serve-ui.ts [--port 8080] [--host 127.0.0.1] [--fake] [--ws 7777] [--autoplay MS]
import fs from 'node:fs';
import http from 'node:http';
import { stripTypeScriptTypes } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = path.dirname(fileURLToPath(import.meta.url));
const ROOTS: Record<string, string> = { '/ui/': path.join(WEB, 'ui'), '/src/': path.join(WEB, 'src'), '/contract/': path.join(WEB, '..', 'contract') };
const TYPES: Record<string, string> = { '.html': 'text/html', '.css': 'text/css', '.ts': 'text/javascript', '.js': 'text/javascript', '.svg': 'image/svg+xml' };

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
  const url = new URL(req.url ?? '/', 'http://x');
  if (url.pathname === '/') { res.writeHead(302, { location: `/ui/${url.search}` }); res.end(); return; }
  const target = url.pathname.endsWith('/') ? url.pathname + 'index.html' : url.pathname;
  let file: string | undefined;
  try { file = resolve(target); } catch { file = undefined; }
  if (!file) { res.writeHead(404, { 'content-type': 'text/plain' }); res.end('not found'); return; }
  const ext = path.extname(file);
  try {
    const body = ext === '.ts' ? stripTypeScriptTypes(fs.readFileSync(file, 'utf8')) : fs.readFileSync(file);
    res.writeHead(200, { 'content-type': `${TYPES[ext] ?? 'application/octet-stream'}; charset=utf-8`, 'cache-control': 'no-store' });
    res.end(body);
  } catch (e) { res.writeHead(500, { 'content-type': 'text/plain' }); res.end(String((e as Error).message)); }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const arg = (n: string, d: string) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1] : d; };
  const host = arg('host', '127.0.0.1'), port = Number(arg('port', '8080'));
  if (process.argv.includes('--fake')) {
    const { FakeDaemon } = await import('./fake-daemon/daemon.ts');
    const { serveWs } = await import('./fake-daemon/serve.ts');
    const daemon = new FakeDaemon();
    const ms = Number(arg('autoplay', '1000'));
    if (ms > 0) daemon.autoplay(ms);
    serveWs(daemon, Number(arg('ws', '7777')), host);
    console.log(`fake daemon: ws://${host}:${arg('ws', '7777')}`);
  }
  http.createServer(handler).listen(port, host, () => console.log(`web UI: http://${host}:${port}/`));
}
