import { test } from 'node:test';
import assert from 'node:assert/strict';
import { trayMenu } from '../src/tray.ts';

const calls: string[] = [];
const actions = { open: () => calls.push('open'), startDaemon: () => calls.push('start'), quit: () => calls.push('quit') };
const labels = (s: Parameters<typeof trayMenu>[0], reason = '') => trayMenu(s, reason, actions).map((i) => i.label ?? '-');

test('the menu opens the window, shows the daemon status and quits', () => {
  assert.deepEqual(labels('running'), ['Open Handline', '-', 'Daemon: running', '-', 'Quit Handline']);
  const items = trayMenu('running', '', actions);
  items[0]!.click!();
  items.at(-1)!.click!();
  assert.deepEqual(calls, ['open', 'quit']);
  assert.equal(items[2]!.enabled, false);
});

test('a stopped or failed daemon offers Start, and a failure says why', () => {
  assert.ok(labels('stopped').includes('Start daemon'));
  assert.ok(labels('failed', 'the gateway exited at start (exit 1)').includes('Daemon: failed (the gateway exited at start (exit 1))'));
  assert.ok(!labels('starting').includes('Start daemon'));
  trayMenu('failed', '', actions).find((i) => i.label === 'Start daemon')!.click!();
  assert.equal(calls.at(-1), 'start');
});
