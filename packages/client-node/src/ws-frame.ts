// One WebSocket frame (RFC 6455) read off the front of a buffer, unmasked; null until the whole frame has arrived.
export interface WsFrame {
  fin: boolean;
  op: number;
  payload: Buffer;
  /** The bytes after this frame. */
  rest: Buffer<ArrayBuffer>;
}

export function readFrame(buf: Buffer<ArrayBuffer>): WsFrame | null {
  if (buf.length < 2) return null;
  let len = buf[1]! & 127,
    off = 2;
  if (len === 126) {
    if (buf.length < 4) return null;
    len = buf.readUInt16BE(2);
    off = 4;
  } else if (len === 127) {
    if (buf.length < 10) return null;
    len = Number(buf.readBigUInt64BE(2));
    off = 10;
  }
  const masked = (buf[1]! & 128) !== 0;
  const start = off + (masked ? 4 : 0);
  if (buf.length < start + len) return null;
  const payload = Buffer.from(buf.subarray(start, start + len));
  if (masked) for (let i = 0; i < payload.length; i++) payload[i]! ^= buf[off + (i & 3)]!;
  return { fin: (buf[0]! & 128) !== 0, op: buf[0]! & 15, payload, rest: buf.subarray(start + len) };
}
