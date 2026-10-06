import type { KV } from '@handline/app-core';

const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

/** The daemon's WebSocket: `?daemon=` or the page's own host (wss on an https page). Plain ws is for a loopback daemon only. */
export function daemonUrl(search: string, loc: { protocol: string; hostname: string }): { url: string; insecure: boolean } {
  const url = new URLSearchParams(search).get('daemon') ?? `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.hostname || '127.0.0.1'}:7777`;
  let insecure = true;
  try { const u = new URL(url); insecure = u.protocol !== 'wss:' && !LOOPBACK.has(u.hostname); } catch { /* not a URL */ }
  return { url, insecure };
}

export function browserStorage(): KV | null {
  try { return localStorage; } catch { return null; }
}

/** A remembered per-viewer setting; blocked storage gives the default and the setting just does not persist. */
export function setting(store: KV | null, key: string, fallback: string): { value: string; save: (v: string) => void } {
  let value = fallback;
  try { value = store?.getItem(key) ?? fallback; } catch { /* blocked */ }
  return { value, save: (v) => { try { store?.setItem(key, v); } catch { /* blocked */ } } };
}
