// Pairing a device with a daemon's network listener: the founder's `device.pair` gives a code and a
// `handline://pair?host=&fp=&code=` uri (a QR on a phone); `redeemPairing` trades the code for a token that
// later hellos carry. The uri's fingerprint is the SHA-256 of the listener's certificate, which the
// transport must check: the certificate is self-signed.
import { DaemonClient } from './client.ts';
import type { Transport } from './transport.ts';

export interface PairingOffer {
  host: string; // address of the listener, host:port
  fingerprint: string; // lower-case hex SHA-256 of the certificate
  code: string;
}

export function parsePairingUri(uri: string): PairingOffer {
  const u = new URL(uri);
  const host = u.searchParams.get('host'), fp = u.searchParams.get('fp'), code = u.searchParams.get('code');
  if (u.protocol !== 'handline:' || u.hostname !== 'pair' || !host || !fp || !code) throw new Error('not a handline pairing uri');
  return { host, fingerprint: fp.toLowerCase(), code };
}

export const listenerUrl = (host: string): string => `wss://${host}`;

/** Trades a pairing code for a device token. `connect` must verify the listener's fingerprint. */
export async function redeemPairing(connect: () => Promise<Transport>, code: string, deviceName: string): Promise<{ token: string; device: string }> {
  const client = new DaemonClient({ connect, client: deviceName, reconnect: false });
  try {
    await client.start();
    const r = await client.call('device.redeem', { code, name: deviceName });
    return { token: r.token, device: r.device };
  } finally { client.close(); }
}
