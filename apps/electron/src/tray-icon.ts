// The tray icon as PNG bytes. nativeImage.createFromDataURL reads only PNG and JPEG, so an SVG data URL gave an empty image and no icon.
// A black ring on transparent, drawn as alpha: with setTemplateImage(true) macOS tints it for a light or a dark menu bar.
import { deflateSync } from 'node:zlib';

const crcTable = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (b: Buffer): number => {
  let c = 0xffffffff;
  for (const x of b) c = crcTable[(c ^ x) & 0xff]! ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};
function chunk(type: string, data: Buffer): Buffer {
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const out = Buffer.alloc(body.length + 8);
  out.writeUInt32BE(data.length, 0);
  body.copy(out, 4);
  out.writeUInt32BE(crc32(body), body.length + 4);
  return out;
}

/** The share of pixel (x, y) inside the ring, from 4×4 samples. */
function coverage(x: number, y: number, size: number, inner: number, outer: number): number {
  let hit = 0;
  for (let i = 0; i < 16; i++) {
    const d = Math.hypot(x + ((i % 4) + 0.5) / 4 - size / 2, y + (Math.floor(i / 4) + 0.5) / 4 - size / 2);
    if (d <= outer && d >= inner) hit++;
  }
  return hit / 16;
}

/** A `size`×`size` ring, 8-bit grey+alpha, edges anti-aliased by 4×4 sampling. */
export function ringPng(size: number): Buffer {
  const outer = size * 0.42;
  const inner = size * 0.26;
  const rows: Buffer[] = [];
  for (let y = 0; y < size; y++) {
    const row = Buffer.alloc(1 + size * 2); // filter byte 0, then (grey, alpha) per pixel
    for (let x = 0; x < size; x++) {
      row[2 + x * 2] = Math.round(coverage(x, y, size, inner, outer) * 255);
    }
    rows.push(row);
  }
  const head = Buffer.alloc(13);
  head.writeUInt32BE(size, 0);
  head.writeUInt32BE(size, 4);
  head[8] = 8;
  head[9] = 4; // grey + alpha
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', head),
    chunk('IDAT', deflateSync(Buffer.concat(rows))),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/** The menu bar's size: 18 pt, drawn at 36 px for a Retina screen. */
export const TRAY_PNG = { pt: 18, scale: 2, bytes: (): Buffer => ringPng(36) } as const;
