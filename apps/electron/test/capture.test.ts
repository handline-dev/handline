import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Sender } from '#src/bridge.ts';
import { Capture, type Source } from '#src/capture.ts';

const ORIGIN = 'http://127.0.0.1:5000';
const top: Sender = { senderFrame: { url: `${ORIGIN}/`, parent: null } };
const frame: Sender = { senderFrame: { url: `${ORIGIN}/`, parent: {} } };
const src = (id: string, name: string, empty = false, bytes = 4): Source => ({
  id,
  name,
  thumbnail: { isEmpty: () => empty, toPNG: () => Buffer.alloc(bytes, 1), toDataURL: () => `data:image/png;base64,${id}` },
});
const cap = (sources: Source[] | Error) =>
  new Capture(ORIGIN, {
    sources: async () => {
      if (sources instanceof Error) throw sources;
      return sources;
    },
  });

test('the list has a picture per window, and empty ones are left out', async () => {
  const r = await cap([src('window:1', 'Simulator'), src('window:2', 'Hidden', true)]).windows(top);
  assert.deepEqual(r, { ok: true, windows: [{ id: 'window:1', name: 'Simulator', thumb: 'data:image/png;base64,window:1' }] });
});

test('every picture empty means the screen permission is missing', async () => {
  const r = await cap([src('window:1', 'A', true)]).windows(top);
  assert.equal(r.ok === false && r.code, 'no_permission');
});

test('one window comes back as base64 PNG with a safe name', async () => {
  const r = await cap([src('window:1', '../Sim: ulator?')]).take(top, { id: 'window:1' });
  assert.deepEqual(r, { ok: true, name: 'Sim ulator.png', png: Buffer.alloc(4, 1).toString('base64') });
});

test('an unknown id, a bad argument, a sub-frame and a huge picture are refused', async () => {
  assert.equal(((await cap([src('a', 'A')]).take(top, { id: 'b' })) as { code: string }).code, 'not_found');
  assert.equal(((await cap([]).take(top, {})) as { code: string }).code, 'bad_request');
  assert.equal(((await cap([]).windows(frame)) as { code: string }).code, 'bad_request');
  assert.equal(((await cap([src('a', 'A', false, 11 * 1024 * 1024)]).take(top, { id: 'a' })) as { code: string }).code, 'too_large');
  assert.equal(((await cap(new Error('boom')).windows(top)) as { code: string }).code, 'unavailable');
});
