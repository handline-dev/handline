import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml',
  '.json': 'application/json', '.png': 'image/png', '.ico': 'image/x-icon', '.woff2': 'font/woff2',
};

/** The built web app on a loopback port (its assets are root-relative, so file:// would not load them). */
export function serveDir(root: string, port = 0): Promise<{ url: string; close: () => void }> {
  const base = path.resolve(root);
  const server = http.createServer((req, res) => {
    let rel: string;
    try { rel = decodeURIComponent((req.url ?? '/').split('?')[0]); }
    catch { res.statusCode = 400; res.end('bad url'); return; }
    let file = path.join(base, rel === '/' ? 'index.html' : rel);
    if (!file.startsWith(base + path.sep) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(base, 'index.html');
    res.setHeader('content-type', TYPES[path.extname(file)] ?? 'application/octet-stream');
    const body = fs.createReadStream(file);
    body.on('error', () => res.destroy());
    body.pipe(res);
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => {
    const { port: p } = server.address() as { port: number };
    resolve({ url: `http://127.0.0.1:${p}/`, close: () => server.close() });
  }));
}
