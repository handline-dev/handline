import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

/** `HANDLINE_FAKE=1 vite` also runs the fake daemon: a WebSocket on 127.0.0.1:HANDLINE_FAKE_PORT (7790), for developing without a real daemon. */
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
      const port = Number(process.env.HANDLINE_FAKE_PORT ?? 7790);
      serveWs(daemon, port);
      console.log(`fake daemon: ws://127.0.0.1:${port}  (open the app with ?daemon=ws://127.0.0.1:${port})`);
    },
  };
}

export default defineConfig({
  plugins: [fakeDaemon()],
  server: { host: '127.0.0.1' },
  test: { environment: 'happy-dom', setupFiles: ['test/setup.ts'], include: ['test/**/*.test.tsx'], testTimeout: 20_000 },
});
