import assert from 'node:assert/strict';
import test from 'node:test';
import { inflateSync } from 'node:zlib';
import { ringPng, TRAY_PNG } from '../src/tray-icon.ts';

test('the tray icon is a real PNG at the menu bar size, a ring of black alpha with a clear middle', () => {
  const png = TRAY_PNG.bytes();
  assert.deepEqual([...png.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  assert.equal(png.readUInt32BE(16), TRAY_PNG.pt * TRAY_PNG.scale);
  assert.equal(png.readUInt32BE(20), TRAY_PNG.pt * TRAY_PNG.scale);
  const small = ringPng(8);
  const idat = small.indexOf('IDAT');
  const raw = inflateSync(small.subarray(idat + 4, idat + 4 + small.readUInt32BE(idat - 4)));
  const alpha = (x: number, y: number) => raw[y * 17 + 1 + x * 2 + 1]!;
  assert.equal(alpha(4, 4), 0);
  assert.ok(alpha(4, 1) > 200);
});
