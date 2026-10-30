import { test } from 'node:test';
import assert from 'node:assert/strict';
import { REMIND_MS, UpdateFlow } from '#src/update-flow.ts';

test('preparing, ready, failed and idle each say so to the listeners, with the step and the reason', () => {
  const f = new UpdateFlow();
  const seen: string[] = [];
  f.onChange(() => seen.push(`${f.view.phase}|${f.view.step}|${f.view.reason}`));
  f.preparing('Building (2 of 3)', 0.66);
  f.ready(() => undefined);
  f.failed('vite failed', () => undefined, 'log text');
  f.idle();
  f.idle(); // already idle: nothing to say
  assert.deepEqual(seen, ['preparing|Building (2 of 3)|', 'ready||', 'failed||vite failed', 'idle||']);
  assert.equal(f.log, 'log text');
});

test('restart and retry run what the source gave, only in the phase that has them; later only puts off a ready update', () => {
  const f = new UpdateFlow();
  const did: string[] = [];
  f.act('restart');
  f.act('retry');
  f.later();
  assert.deepEqual([did.length, f.view.phase], [0, 'idle']);
  f.ready(() => did.push('restart'));
  f.act('retry'); // nothing failed
  f.act('restart');
  assert.deepEqual(did, ['restart']);
  f.act('later');
  assert.equal(f.view.phase, 'later');
  f.act('restart'); // the bell row keeps Restart now
  assert.deepEqual(did, ['restart', 'restart']);
  f.failed('x', () => did.push('retry'));
  f.act('retry');
  f.act('later'); // a failure is not put off
  assert.deepEqual([did.at(-1), f.view.phase], ['retry', 'failed']);
});

test('a put-off update is raised again only after a day', () => {
  let t = 0;
  const f = new UpdateFlow(() => t);
  f.ready(() => undefined);
  f.later();
  t = REMIND_MS - 1;
  assert.equal(f.remind(), false);
  t = REMIND_MS;
  assert.equal(f.remind(), true);
  assert.equal(f.view.phase, 'ready');
  assert.equal(f.remind(), false);
});
