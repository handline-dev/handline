// @vitest-environment node
// The dev-only pairing link: `handline://pair?host=…&code=…&plain=1` pairs to a plain ws listener, and only in a dev build.
import type { AddressInfo } from 'node:net';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { FakeDaemon } from '@handline/fake-daemon/daemon';
import { serveWs } from '@handline/fake-daemon/serve';

vi.mock('#modules/pinned-socket/index.ts', () => ({ default: {} }));
import { pairFromDevLink } from '#src/session.ts';

const g = globalThis as { __DEV__?: boolean };
const daemon = new FakeDaemon();
const server = serveWs(daemon, 0, '127.0.0.1', undefined, true);
let host = '';
beforeAll(async () => {
  if (!server.listening) await new Promise((r) => server.once('listening', r));
  host = `127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterAll(() => void server.close());

const link = (extra: string) => `handline://pair?host=${host}&code=${daemon.issueCode()}${extra}`;

describe('pairFromDevLink', () => {
  it('pairs to a plain listener with no fingerprint in a dev build', async () => {
    g.__DEV__ = true;
    const p = await pairFromDevLink(link('&plain=1'));
    expect(p).toMatchObject({ host, fingerprint: null });
    expect(p?.token).toMatch(/^hld_/);
  });

  it('ignores a release build, a link without plain=1 and a link that is not a pairing', async () => {
    g.__DEV__ = false;
    expect(await pairFromDevLink(link('&plain=1'))).toBeNull();
    g.__DEV__ = true;
    expect(await pairFromDevLink(link(''))).toBeNull();
    expect(await pairFromDevLink('handline://other?host=h&code=c&plain=1')).toBeNull();
    expect(await pairFromDevLink('not a url')).toBeNull();
  });
});
