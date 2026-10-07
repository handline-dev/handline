// Pairing a device with a daemon's network listener: the founder's `device.pair` gives a code and a
// `handline://pair?host=&fp=&code=` uri (a QR on a phone); `redeemPairing` trades the code for a token that
// later hellos carry. The uri's fingerprint is the SHA-256 of the listener's certificate, which the
// transport must check: the certificate is self-signed. A plain gateway on a Tailscale address has no certificate and no
// fingerprint: Tailscale's own tunnel encrypts it, and it is served as ws://.
import { RpcError } from './errors.ts';
import type { ErrorCode } from './protocol.ts';
import type { Transport } from './transport.ts';

export interface PairingOffer {
  host: string; // address of the listener, host:port
  fingerprint: string | null; // lower-case hex SHA-256 of the certificate; null for a plain Tailscale gateway
  code: string;
}

/** A Tailscale address: 100.64.0.0/10 or a *.ts.net name. `host` is host:port. */
export function isTailscaleHost(host: string): boolean {
  const name = host.replace(/:\d+$/, '').replace(/\.$/, '').toLowerCase();
  if (name.endsWith('.ts.net')) return true;
  const ip = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(name);
  return !!ip && ip.slice(1).every((n) => +n <= 255) && +ip[1] === 100 && +ip[2] >= 64 && +ip[2] <= 127;
}

export function parsePairingUri(uri: string): PairingOffer {
  const u = new URL(uri);
  const host = u.searchParams.get('host'),
    fp = u.searchParams.get('fp'),
    code = u.searchParams.get('code');
  if (u.protocol !== 'handline:' || u.hostname !== 'pair' || !host || !code) throw new Error('not a handline pairing uri');
  if (fp) return { host, fingerprint: fp.toLowerCase(), code };
  if (!isTailscaleHost(host))
    throw new Error(`this pairing link has no certificate fingerprint, and ${host} is not a Tailscale address, so it cannot be trusted`);
  return { host, fingerprint: null, code };
}

/** Where the listener serves its WebSocket: wss with a pinned certificate, ws for a plain Tailscale gateway (`pinned` false). */
export const listenerUrl = (host: string, pinned = true): string => `${pinned ? 'wss' : 'ws'}://${host}/rpc`;

/**
 * Trades a pairing code for a device token. `connect` must verify the listener's fingerprint. An unpaired
 * connection may call nothing but device.redeem, hello included, so this is the first and only message sent.
 */
export async function redeemPairing(
  connect: () => Promise<Transport>,
  code: string,
  deviceName: string,
  timeoutMs = 10_000,
): Promise<{ token: string; device: string }> {
  const t = await connect();
  const request_id = `pair-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const reply = await new Promise<Reply>((ok, fail) => {
    timer = setTimeout(() => fail(new RpcError('transport', 'the listener did not answer')), timeoutMs);
    t.onmessage = (m) => {
      try {
        ok(JSON.parse(m));
      } catch {
        fail(new RpcError('transport', 'the listener sent something that is not JSON'));
      }
    };
    t.onclose = () => fail(new RpcError('transport', 'the listener closed the connection'));
    t.send(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'device.redeem', params: { code, name: deviceName, request_id } }));
  }).finally(() => {
    clearTimeout(timer);
    t.onclose = null;
    t.close();
  });
  if (reply.error)
    throw new RpcError(
      (reply.error.data?.code ?? 'refused') as ErrorCode,
      reply.error.data?.reason ?? reply.error.message,
      reply.error.data,
    );
  return { token: reply.result!.token, device: reply.result!.device };
}

interface Reply {
  result?: { token: string; device: string };
  error?: { message: string; data?: { code?: string; gate?: string; reason?: string } };
}
