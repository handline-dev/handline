import type { KV } from '@handline/app-core';

const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

/**
 * The daemon's WebSocket: `?daemon=`, else in a built page the gateway that served it (`/rpc` on the page's own origin, which a
 * device token opens), else the fake daemon's port in development. Plain ws is for a loopback daemon or the page's own origin
 * only; a gateway that is not on loopback should be served over https, or its device token travels in clear.
 */
export function daemonUrl(
  search: string,
  loc: { protocol: string; host: string; hostname: string },
  built = false,
): { url: string; insecure: boolean } {
  const ws = loc.protocol === 'https:' ? 'wss' : 'ws';
  const given = new URLSearchParams(search).get('daemon');
  if (given === null && built) return { url: `${ws}://${loc.host}/rpc`, insecure: false };
  const url = given ?? `${ws}://${loc.hostname || '127.0.0.1'}:7777`;
  let insecure = true;
  try {
    const u = new URL(url);
    insecure = u.protocol !== 'wss:' && !LOOPBACK.has(u.hostname);
  } catch {
    /* not a URL */
  }
  return { url, insecure };
}

export function browserStorage(): KV | null {
  try {
    return localStorage;
  } catch {
    return null;
  }
}

/** A remembered per-viewer setting; blocked storage gives the default and the setting just does not persist. */
export function setting(store: KV | null, key: string, fallback: string): { value: string; save: (v: string) => void } {
  let value = fallback;
  try {
    value = store?.getItem(key) ?? fallback;
  } catch {
    /* blocked */
  }
  return {
    value,
    save: (v) => {
      try {
        store?.setItem(key, v);
      } catch {
        /* blocked */
      }
    },
  };
}
