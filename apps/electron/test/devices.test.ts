import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Sender } from '#src/bridge.ts';
import { Devices, adbDevices, adbPath, androidText, bootedSims, pngSize } from '#src/devices.ts';

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
  const d = new Devices(
    ORIGIN,
    async (file, args) => {
      calls.push([file, ...args]);
      const r = replies[args.join(' ')];
      if (r instanceof Error || !r) throw r ?? new Error('no reply');
      return r;
    },
    'adb',
  );
  return { d, calls };
}

test('the picture size comes from the PNG header', () => {
  assert.deepEqual(pngSize(png), { width: 1080, height: 2400 });
  assert.equal(pngSize(Buffer.from('nope')), null);
});

test('adb comes from the SDK when it is there, else from the PATH', () => {
  assert.equal(
    adbPath({ ANDROID_HOME: '/sdk' }, (p) => p === '/sdk/platform-tools/adb'),
    '/sdk/platform-tools/adb',
  );
  assert.equal(
    adbPath({}, () => false),
    'adb',
  );
});

test('an Android picture needs a running device and says plainly when there is none', async () => {
  const none = make({ devices: Buffer.from('List of devices attached\n\n') });
  const r = await none.d.shot(top, { kind: 'android', id: '' });
  assert.equal(r.ok, false);
  assert.match((r as { message: string }).message, /No Android emulator or device is running/);
  const some = make({ devices: Buffer.from('List of devices attached\nemulator-5554\tdevice\n'), 'exec-out screencap -p': png });
  assert.deepEqual(await some.d.shot(top, { kind: 'android', id: '' }), { ok: true, png: png.toString('base64') });
});

test('a missing adb is told apart from a missing device', async () => {
  const { d } = make({ devices: Object.assign(new Error('x'), { code: 'ENOENT' }) });
  assert.match(((await d.shot(top, { kind: 'android', id: '' })) as { message: string }).message, /adb is not installed/);
});

test('a tap is a place in the last picture of that device, scaled to its pixels', async () => {
  const { d, calls } = make({
    devices: Buffer.from('x\nemulator-5554\tdevice\n'),
    '-s emulator-5554 exec-out screencap -p': png,
    '-s emulator-5554 shell input tap 540 600': Buffer.alloc(0),
  });
  const tap = { kind: 'android', id: 'emulator-5554', x: 0.5, y: 0.25 };
  assert.equal((await d.tap(top, tap)).ok, false); // no picture yet
  await d.shot(top, { kind: 'android', id: 'emulator-5554' });
  assert.deepEqual(await d.tap(top, tap), { ok: true });
  assert.deepEqual(calls.at(-1), ['adb', '-s', 'emulator-5554', 'shell', 'input', 'tap', '540', '600']);
});

test('a swipe and typed text go to the picked Android device, the text as one quoted word', async () => {
  const { d, calls } = make({
    devices: Buffer.from('x\nR58M\tdevice\n'),
    '-s R58M exec-out screencap -p': png,
    '-s R58M shell input swipe 540 1800 540 600 300': Buffer.alloc(0),
    "-s R58M shell input text 'it%ss'\\''s'": Buffer.alloc(0),
  });
  const dev = { kind: 'android', id: 'R58M' };
  await d.shot(top, dev);
  assert.deepEqual(await d.swipe(top, { ...dev, x1: 0.5, y1: 0.75, x2: 0.5, y2: 0.25 }), { ok: true });
  assert.deepEqual(calls.at(-1), ['adb', '-s', 'R58M', 'shell', 'input', 'swipe', '540', '1800', '540', '600', '300']);
  assert.deepEqual(await d.text(top, { ...dev, text: "it s's" }), { ok: true });
  assert.equal(androidText("it s's"), "'it%ss'\\''s'");
  assert.equal((await d.text(top, { ...dev, text: 'two\nlines' })).ok, false);
});

test('a simulator is driven through idb in points, and idb missing says how to install it', async () => {
  const describe = JSON.stringify({ screen_dimensions: { width_points: 393, height_points: 852 } });
  const with_ = make({
    'simctl io UDID-1 screenshot --type=png /dev/null': png,
    'describe --udid UDID-1 --json': Buffer.from(describe),
    'ui tap 197 213 --udid UDID-1': Buffer.alloc(0),
  });
  // the picture is read from a temp file, so seed the size the way a shot would
  (with_.d as unknown as { sizes: Map<string, unknown> }).sizes.set('ios:UDID-1', { width: 1179, height: 2556 });
  assert.deepEqual(await with_.d.tap(top, { kind: 'ios', id: 'UDID-1', x: 0.5, y: 0.25 }), { ok: true });
  assert.deepEqual(with_.calls.at(-1), ['idb', 'ui', 'tap', '197', '213', '--udid', 'UDID-1']);
  const without = make({});
  (without.d as unknown as { sizes: Map<string, unknown> }).sizes.set('ios:UDID-1', { width: 1179, height: 2556 });
  const r = (await without.d.tap(top, { kind: 'ios', id: 'UDID-1', x: 0.5, y: 0.25 })) as { ok: false; message: string };
  assert.equal(r.ok, false);
  assert.match(r.message, /No iOS simulator is booted|idb/);
});

test('the list has the Android devices and the booted simulators, and says whether idb is there', async () => {
  const sims = JSON.stringify({
    devices: {
      'iOS-18': [
        { udid: 'U1', name: 'iPhone 16', state: 'Booted' },
        { udid: 'U2', name: 'iPad', state: 'Shutdown' },
      ],
    },
  });
  assert.deepEqual(bootedSims(sims), [{ kind: 'ios', id: 'U1', name: 'iPhone 16' }]);
  assert.deepEqual(adbDevices('List of devices attached\nemulator-5554\tdevice\nR58M\toffline\n'), [
    { kind: 'android', id: 'emulator-5554', name: 'emulator-5554' },
  ]);
  const { d } = make({
    devices: Buffer.from('x\nR58M\tdevice\n'),
    'simctl list devices booted -j': Buffer.from(sims),
    '--help': Buffer.from('usage'),
  });
  assert.deepEqual(await d.list(top), {
    ok: true,
    devices: [
      { kind: 'android', id: 'R58M', name: 'R58M' },
      { kind: 'ios', id: 'U1', name: 'iPhone 16' },
    ],
    idb: true,
  });
  assert.equal((await d.list(other)).ok, false);
});

test('only the app window asks, a device is android or ios, and a place is inside the picture', async () => {
  const { d } = make({});
  assert.equal((await d.shot(other, { kind: 'android', id: '' })).ok, false);
  assert.equal((await d.shot(top, { kind: 'rm -rf', id: '' })).ok, false);
  assert.equal((await d.shot(top, { kind: 'android', id: '; reboot' })).ok, false);
  assert.equal((await d.shot(top, { kind: 'ios', id: '--help' })).ok, false); // never an option of the command
  assert.equal((await d.tap(top, { kind: 'android', id: '', x: 2, y: 0 })).ok, false);
  assert.equal((await d.tap(other, { kind: 'android', id: '', x: 0, y: 0 })).ok, false);
});

test('an iOS picture says plainly when no simulator is booted', async () => {
  const { d } = make({});
  assert.match(((await d.shot(top, { kind: 'ios', id: '' })) as { message: string }).message, /No iOS simulator is booted/);
});

test('an iOS tap with no simulator picked is refused, not sent to idb with an empty id', async () => {
  const { d, calls } = make({});
  (d as unknown as { sizes: Map<string, unknown> }).sizes.set('ios:', { width: 1179, height: 2556 });
  const r = (await d.tap(top, { kind: 'ios', id: '', x: 0.5, y: 0.5 })) as { ok: false; message: string };
  assert.match(r.message, /Pick a simulator/);
  assert.equal(calls.length, 0);
});
