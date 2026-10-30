import { DaemonClient, RpcError, redeemPairing, webSocketTransport, type Transport } from '@handline/client';
import type { KV } from '@handline/app-core';

const key = (url: string) => `token:${url}`;

/** The network listener serves its socket at /rpc; the Python daemon's own port has no path and takes no token. */
export const needsToken = (url: string): boolean => {
  try {
    return new URL(url).pathname === '/rpc';
  } catch {
    return false;
  }
};

export function loadToken(store: KV | null, url: string): string | null {
  try {
    return store?.getItem(key(url)) || null;
  } catch {
    return null;
  }
}

/** The code out of what the person pasted: `hl device pair` prints the code and a handline:// uri that holds it. */
export function codeFrom(text: string): string {
  const t = text.trim();
  try {
    const c = new URL(t).searchParams.get('code');
    if (c) return c;
  } catch {
    /* a bare code */
  }
  return t;
}

/** The code an invite link carries: the page opened as `/#pair=<code>`. Null for any other hash. */
export function inviteCode(hash: string): string | null {
  const c = /^#pair=([^&]+)/.exec(hash)?.[1];
  try {
    return c ? decodeURIComponent(c) : null;
  } catch {
    return null;
  }
}

/** What the device list calls this browser: the browser on the system, so the person can tell which one to revoke. */
export function deviceName(agent: string = navigator.userAgent): string {
  const browser = /Edg\//.test(agent)
    ? 'Edge'
    : /Firefox\//.test(agent)
      ? 'Firefox'
      : /Chrome\//.test(agent)
        ? 'Chrome'
        : /Safari\//.test(agent)
          ? 'Safari'
          : 'Browser';
  const system = /iPhone|iPad/.test(agent)
    ? 'iPhone'
    : /Android/.test(agent)
      ? 'Android'
      : /Mac OS/.test(agent)
        ? 'Mac'
        : /Windows/.test(agent)
          ? 'Windows'
          : /Linux/.test(agent)
            ? 'Linux'
            : '';
  return system ? `${browser} on ${system}` : browser;
}

/** Trades the pasted code for a device token and keeps it for this listener. Throws the daemon's own sentence on a refusal. */
export async function pairWith(
  url: string,
  pasted: string,
  store: KV | null,
  open: (url: string) => Promise<Transport> = webSocketTransport,
  name = 'Handline desktop',
): Promise<string> {
  const { token } = await redeemPairing(() => open(url), codeFrom(pasted), name);
  try {
    store?.setItem(key(url), token);
  } catch {
    /* blocked: paired for this run only */
  }
  return token;
}

export function forgetToken(store: KV | null, url: string): void {
  try {
    store?.setItem(key(url), ''); // KV has no remove; loadToken reads an empty one as none
  } catch {
    /* blocked */
  }
}

/** Whether the listener still knows this token: only its fixed `unpaired` refusal says no; a listener that cannot be reached says nothing. */
export async function tokenRefused(url: string, token: string, open: (url: string) => Promise<Transport>): Promise<boolean> {
  const c = new DaemonClient({ client: 'web-pc', token, connect: () => open(url) });
  try {
    await c.start();
    return false;
  } catch (e) {
    return e instanceof RpcError && e.gate === 'unpaired';
  } finally {
    c.close();
  }
}
