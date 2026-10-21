import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from '#src/helpers.ts';

test('daemon.status carries what each agent and the machine use', async () => {
  const { client } = setup();
  await client.start();
  const { resources, agents } = await client.call('daemon.status', {});
  assert.ok(resources);
  assert.equal(Object.keys(resources.agents).length, agents.total);
  for (const use of Object.values(resources.agents)) assert.ok(Number.isInteger(use.rss_bytes) && use.cpu_pct >= 0);
  assert.ok(resources.machine.disk_free_bytes > 0 && resources.machine.cores > 0);
  client.close();
});
