import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Sender } from '#src/bridge.ts';
import { Devices, adbPath, pngSize } from '#src/devices.ts';

const ORIGIN = 'http://127.0.0.1:5000';
const top: Sender = { senderFrame: { url: `${ORIGIN}/`, parent: null } };
const other: Sender = { senderFrame: { url: 'http://evil.example/', parent: null } };

/** A PNG header that says 1080 x 2400, then nothing the tests read. */
const png = Buffer.alloc(32);
png.writeUInt32BE(0x49484452, 12);
png.writeUInt32BE(1080, 16);
png.writeUInt32BE(2400, 20);

function make(replies: Record<string, Buffer | Error>) {
  const calls: string[][] = [];
  const d = new Devices(ORIGIN, async (file, args) => {
    calls.push([file, ...args]);
    const r = replies[args.join(' ')];
    if (r instanceof Error || !r) throw r ?? new Error('no reply');
    return r;
  }, 'adb');
  return { d, calls };
}

test('the picture size comes from the PNG header', () => {
  assert.deepEqual(pngSize(png), { width: 1080, height: 2400 });
  assert.equal(pngSize(Buffer.from('nope')), null);
});

test('adb comes from the SDK when it is there, else from the PATH', () => {
  assert.equal(adbPath({ ANDROID_HOME: '/sdk' }, (p) => p === '/sdk/platform-tools/adb'), '/sdk/platform-tools/adb');
  assert.equal(adbPath({}, () => false), 'adb');
});

test('an Android picture needs a running device and says plainly when there is none', async () => {
  const none = make({ devices: Buffer.from('List of devices attached\n\n') });
  const r = await none.d.shot(top, 'android');
  assert.equal(r.ok, false);
  assert.match((r as { message: string }).message, /No Android emulator or device is running/);
  const some = make({ devices: Buffer.from('List of devices attached\nemulator-5554\tdevice\n'), 'exec-out screencap -p': png });
  assert.deepEqual(await some.d.shot(top, 'android'), { ok: true, png: png.toString('base64') });
});

test('a missing adb is told apart from a missing device', async () => {
  const { d } = make({ devices: Object.assign(new Error('x'), { code: 'ENOENT' }) });
  assert.match(((await d.shot(top, 'android')) as { message: string }).message, /adb is not installed/);
});

test('a tap is a place in the last picture, scaled to its pixels', async () => {
  const { d, calls } = make({ devices: Buffer.from('x\nemulator-5554\tdevice\n'), 'exec-out screencap -p': png, 'shell input tap 540 600': Buffer.alloc(0) });
  assert.equal((await d.tap(top, { x: 0.5, y: 0.25 })).ok, false); // no picture yet
  await d.shot(top, 'android');
  assert.deepEqual(await d.tap(top, { x: 0.5, y: 0.25 }), { ok: true });
  assert.deepEqual(calls.at(-1), ['adb', 'shell', 'input', 'tap', '540', '600']);
});

test('only the app window asks, and only for the two kinds with a tap inside the picture', async () => {
  const { d } = make({});
  assert.equal((await d.shot(other, 'android')).ok, false);
  assert.equal((await d.shot(top, 'rm -rf')).ok, false);
  assert.equal((await d.tap(top, { x: 2, y: 0 })).ok, false);
  assert.equal((await d.tap(other, { x: 0, y: 0 })).ok, false);
});

test('an iOS picture says plainly when no simulator is booted', async () => {
  const { d } = make({});
  assert.match(((await d.shot(top, 'ios')) as { message: string }).message, /No iOS simulator is booted/);
});
