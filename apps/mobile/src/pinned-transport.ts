// A client Transport over the pinned-socket native module: React Native's own WebSocket only trusts what the OS trusts,
// and the daemon's certificate is self-signed, trusted by its fingerprint alone.
import type { Transport } from '@handline/client';
import type { PinnedSocketNative } from '../modules/pinned-socket/index.ts';

let next = 0;

export async function pinnedTransport(native: PinnedSocketNative, url: string, fingerprint: string): Promise<Transport> {
  const id = `c${++next}`;
  const early: string[] = []; // messages that arrive before the client has set onmessage
  let closed = false;
  const t: Transport = {
    onclose: null,
    send: (m) => { if (!closed) native.send(id, m); },
    close: () => { if (!closed) native.close(id); },
    get onmessage() { return handler; },
    set onmessage(fn) { handler = fn; if (fn) for (const m of early.splice(0)) fn(m); },
  };
  let handler: ((m: string) => void) | null = null;
  const subs = [
    native.addListener('message', (e) => { if (e.id !== id) return; if (handler) handler(e.data); else early.push(e.data); }),
    native.addListener('close', (e) => { if (e.id !== id || closed) return; closed = true; for (const s of subs) s.remove(); t.onclose?.(); }),
  ];
  try { await native.connect(id, url, fingerprint); }
  catch (e) { closed = true; for (const s of subs) s.remove(); throw new Error(`cannot connect to ${url}: ${(e as Error).message}`); }
  return t;
}
