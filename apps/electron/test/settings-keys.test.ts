import { test } from 'node:test';
import assert from 'node:assert/strict';
import { capField, idleField, limitFields, paceFields, DEFAULT_LIMITS, type PageSettings } from '@handline/app-core';
import { Settings, type Sender } from '#src/bridge.ts';

const ORIGIN = 'http://127.0.0.1:1';
const window: Sender = { senderFrame: { url: `${ORIGIN}/`, parent: null } };

test('every change the settings field builders write passes the shell bridge', async () => {
  const sent: unknown[] = [];
  const s = new Settings(ORIGIN, { get: async () => ({}) as never, set: async (c) => (sent.push(c), { accepted: true } as never) });
  const all = { host_active_cap: 1, load_per_core_pct: 1, ram_free_min_pct: 1, pacing: true, pace_target_pct: 1, pace_burst_pct: 1 };
  const changes: PageSettings[] = [
    capField(1).change(2),
    idleField(60).change(0),
    ...limitFields(all).map((f) => f.change(null)),
    ...paceFields(all).map((f) => f.change(80)),
    ...Object.keys(DEFAULT_LIMITS).map((k) => ({ [k]: 1 }) as PageSettings),
    { keep_awake: 'on' } as unknown as PageSettings,
  ];
  for (const c of changes)
    assert.deepEqual(await s.set(window, { changes: c }), { ok: true, result: { accepted: true } }, JSON.stringify(c));
  assert.equal(sent.length, changes.length);
});
