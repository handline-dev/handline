import http from 'node:http';
import tailwindcss from '@tailwindcss/vite';
import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

/** `HANDLINE_FAKE=1 vite` also runs the fake daemon (`HANDLINE_FAKE_VERBS=1` adds the task verbs): a WebSocket on 127.0.0.1:HANDLINE_FAKE_PORT (7790), for developing without a real daemon. */
function fakeDaemon(): Plugin {
  return {
    name: 'handline-fake-daemon',
    apply: 'serve',
    async configureServer() {
      if (!process.env.HANDLINE_FAKE) return;
      const { FakeDaemon } = await import('@handline/fake-daemon/daemon');
      const { serveWs } = await import('@handline/fake-daemon/serve');
      const daemon = new FakeDaemon();
      daemon.autoplay(4000);
      if (process.env.HANDLINE_FAKE_VERBS) daemon.tasks.verbs = true; // serve the task verbs of the daemon's plan too
      const port = Number(process.env.HANDLINE_FAKE_PORT ?? 7790);
      serveWs(daemon, port);
      console.log(`fake daemon: ws://127.0.0.1:${port}  (open the app with ?daemon=ws://127.0.0.1:${port})`);
    },
  };
}

/** `HANDLINE_GATEWAY_WS=host:port vite` tunnels ws /rpc to that gateway with the Origin the
 * gateway takes (its own) - the same rewrite the desktop shell does for its page - so a
 * vite-served page pairs and runs against the real daemon, with HMR. Dev aid; `serve` only. */
function realGateway(): Plugin {
  const gateway = process.env.HANDLINE_GATEWAY_WS;
  if (!gateway) return { name: 'handline-real-gateway', apply: 'serve' };
  const [host, port] = gateway.split(':');
  return {
    name: 'handline-real-gateway',
    apply: 'serve',
    configureServer(server) {
      server.httpServer?.on('upgrade', (req, socket, head) => {
        if (new URL(req.url ?? '/', 'http://x').pathname !== '/rpc') return;
        const origin = `http://${host}:${port}`;
        const up = http.request({
          host,
          port: Number(port),
          method: 'GET',
          path: req.url,
          headers: { ...req.headers, host: `${host}:${port}`, origin },
        });
        up.on('upgrade', (ures, usocket, _uhead) => {
          const lines = Object.entries(ures.headers).map(([k, v]) => `${k}: ${v}`);
          socket.write(`HTTP/1.1 101 Switching Protocols\r\n${lines.join('\r\n')}\r\n\r\n`);
          usocket.write(head);
          usocket.pipe(socket);
          socket.pipe(usocket);
        });
        up.on('response', (res) => {
          socket.end(`HTTP/1.1 ${res.statusCode} ${res.statusMessage}\r\nconnection: close\r\n\r\n`);
        });
        up.end();
      });
    },
  };
}

export default defineConfig({
  plugins: [tailwindcss(), fakeDaemon(), realGateway()],
  server: { host: '127.0.0.1', watch: { usePolling: true, interval: 300 } },
  test: { environment: 'happy-dom', setupFiles: ['test/setup.ts'], include: ['test/**/*.test.tsx'], testTimeout: 20_000 },
});
