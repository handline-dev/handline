import { test } from 'node:test';
import assert from 'node:assert/strict';
import { relaunchCommand } from '#src/relaunch.ts';

test('the relaunch waits for the old process to end, then runs launch.ts from the electron folder', () => {
  const c = relaunchCommand(123, '/w/apps/electron');
  assert.equal(c.file, 'sh');
  assert.deepEqual(c.args.slice(2), ['sh', '123', '/w/apps/electron']);
  assert.match(c.args[1]!, /kill -0 "\$1"[^;]*; do sleep[^;]*; done; cd "\$2" && exec node launch\.ts/);
});
