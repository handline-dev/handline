// A transport moves whole JSON-RPC messages as strings. Sockets frame them as lines; a WebSocket
// frames them as messages. The client and the fake daemon see only this interface.

export interface Transport {
  send(message: string): void;
  close(): void;
  onmessage: ((message: string) => void) | null;
  onclose: (() => void) | null;
}

/** Splits a byte-stream's text into lines; a partial line waits for the rest. */
export class LineSplitter {
  private rest = '';
  push(chunk: string): string[] {
    const parts = (this.rest + chunk).split('\n');
    this.rest = parts.pop() ?? '';
    return parts.filter((p) => p.trim() !== '');
  }
}

/** Two linked in-memory ends; messages arrive on a later tick like a real socket. */
export function memoryPair(): [Transport, Transport] {
  const make = (): Transport & { peer?: Transport; closed: boolean } => ({
    closed: false, onmessage: null, onclose: null,
    send(m) { const p = this.peer; if (!this.closed && p) queueMicrotask(() => { if (!this.closed) p.onmessage?.(m); }); },
    close() {
      if (this.closed) return;
      this.closed = true;
      const p = this.peer as (Transport & { closed: boolean }) | undefined;
      queueMicrotask(() => { this.onclose?.(); if (p && !p.closed) { p.closed = true; p.onclose?.(); } });
    },
  });
  const a = make(), b = make();
  a.peer = b; b.peer = a;
  return [a, b];
}

/** Browser, Electron renderer and React Native all have a global WebSocket. */
export function webSocketTransport(url: string): Promise<Transport> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const t: Transport = {
      onmessage: null, onclose: null,
      send: (m) => ws.send(m),
      close: () => ws.close(),
    };
    ws.onopen = () => resolve(t);
    ws.onmessage = (e) => t.onmessage?.(String(e.data));
    ws.onclose = () => t.onclose?.();
    ws.onerror = () => reject(new Error(`cannot connect to ${url}`));
  });
}
