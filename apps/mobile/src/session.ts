// What the phone keeps between launches (the pairing: where the daemon is, which certificate to trust, the device token)
// and how it builds the store from it.
import * as SecureStore from 'expo-secure-store';
import { DaemonClient, listenerUrl, parsePairingUri, redeemPairing, webSocketTransport } from '@handline/client';
import { Store } from '@handline/app-core';
import Native from '../modules/pinned-socket/index.ts';
import { pinnedTransport } from './pinned-transport.ts';

export interface Pairing {
  host: string;
  fingerprint: string | null; // null: a plain Tailscale gateway, reached as ws://
  token: string;
  device: string;
}
const KEY = 'handline.pairing';

export async function loadPairing(): Promise<Pairing | null> {
  try {
    const s = await SecureStore.getItemAsync(KEY);
    return s ? (JSON.parse(s) as Pairing) : null;
  } catch {
    return null;
  }
}
export const forgetPairing = () =>
  Promise.all([SecureStore.deleteItemAsync(KEY), SecureStore.deleteItemAsync('handline.new-agent')]).then(() => {});

const connectTo = (host: string, fingerprint: string | null) => () =>
  fingerprint ? pinnedTransport(Native, listenerUrl(host), fingerprint) : webSocketTransport(listenerUrl(host, false));

/** Trades the code in a pairing uri for a token and keeps the result. Throws the daemon's own sentence when it refuses. */
export async function pair(uri: string, deviceName: string): Promise<Pairing> {
  const offer = parsePairingUri(uri.trim());
  // The daemon names input holders by this, so two phones need different names for "take from another device" to ask.
  const name = `${deviceName}-${Math.floor(Math.random() * 0x10000)
    .toString(16)
    .padStart(4, '0')}`;
  const { token, device } = await redeemPairing(connectTo(offer.host, offer.fingerprint), offer.code, name);
  const p = { host: offer.host, fingerprint: offer.fingerprint, token, device };
  await SecureStore.setItemAsync(KEY, JSON.stringify(p));
  return p;
}

export const storeFor = (p: Pairing): Store =>
  new Store(
    new DaemonClient({
      client: 'phone',
      token: p.token,
      reconnect: { minMs: 500, maxMs: 8000 },
      connect: connectTo(p.host, p.fingerprint),
    }),
  );

const NEW_AGENT = 'handline.new-agent';
/** The operation the New agent page was watching, so a relaunch returns to it. */
export const loadOperation = () => SecureStore.getItemAsync(NEW_AGENT).catch(() => null);
export const saveOperation = (op: string | null) =>
  (op ? SecureStore.setItemAsync(NEW_AGENT, op) : SecureStore.deleteItemAsync(NEW_AGENT)).catch(() => {});
