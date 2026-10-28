import { test } from 'node:test';
import assert from 'node:assert/strict';
import { trayIcon, trayMenu, type TrayFollow, type TrayMode } from '#src/tray.ts';

const calls: string[] = [];
const actions = {
  open: () => calls.push('open'),
  startDaemon: () => calls.push('start'),
  quit: () => calls.push('quit'),
  inbox: () => calls.push('inbox'),
  settings: () => calls.push('settings'),
  reconnect: () => calls.push('reconnect'),
  copy: (t: string) => calls.push(`copy ${t}`),
};
type State = Parameters<typeof trayMenu>[0];
const view = (unread = 0, details = false) => ({ unread, details });
const labels = (s: State, reason = '', follow?: TrayFollow, m?: TrayMode, v = view()) =>
  trayMenu(s, reason, actions, follow, m, v).map((i) => i.label ?? '-');
const mode = (over: Partial<TrayMode> = {}): TrayMode => ({
  mode: 'own',
  link: { status: 'connected', address: '127.0.0.1:7443', version: 'abc1234', reason: '', mismatch: () => null },
  switchTo: () => calls.push('switch'),
  pair: () => calls.push('pair'),
  phone: () => calls.push('phone'),
  ...over,
});
const follow = (over: Partial<TrayFollow> = {}): TrayFollow => ({
  sha: 'aaa1111ffff',
  state: 'current',
  next: null,
  reason: '',
  check: () => calls.push('check'),
  restart: () => calls.push('restart'),
  ...over,
});

test('normally the menu is Open, Pair a phone, Settings and Quit, with no status lines', () => {
  assert.deepEqual(labels('running', '', follow(), mode()), ['Open Handline', '-', 'Pair a phone…', 'Settings…', '-', 'Quit Handline']);
  assert.deepEqual(labels('running'), ['Open Handline', '-', 'Settings…', '-', 'Quit Handline']);
  const items = trayMenu('running', '', actions, follow(), mode(), view());
  items[0]!.click!();
  items.find((i) => i.label === 'Settings…')!.click!();
  items.at(-1)!.click!();
  assert.deepEqual(calls.splice(0), ['open', 'settings', 'quit']);
});

test('items waiting on the person add one line that opens the Inbox, and a red dot', () => {
  const l = labels('running', '', undefined, mode(), view(3));
  assert.ok(l.includes('Needs you · 3'));
  assert.ok(trayMenu('running', '', actions, undefined, mode(), view(250)).some((i) => i.label === 'Needs you · 99+'));
  trayMenu('running', '', actions, undefined, mode(), view(3)).find((i) => i.label === 'Needs you · 3')!.click!();
  assert.equal(calls.pop(), 'inbox');
  assert.equal(trayIcon('running', '', actions, view(3), follow(), mode()), 'turn');
});

test('a ready update adds Restart to update and a blue dot; a current one adds nothing', () => {
  const moved = follow({ state: 'available', next: 'bbb2222ffff' });
  assert.ok(labels('running', '', moved, mode()).includes('Restart to update'));
  assert.equal(trayIcon('running', '', actions, view(), moved, mode()), 'update');
  assert.equal(trayIcon('running', '', actions, view(), follow(), mode()), 'plain');
  assert.ok(!labels('running', '', follow(), mode()).some((l) => /update/i.test(l)));
});

test('a stopped or failed daemon shows one warning with its fix and the yellow icon, and a failure says why', () => {
  assert.ok(labels('stopped').includes('Start daemon'));
  assert.ok(
    labels('failed', 'the gateway exited at start (exit 1)').includes('⚠ The daemon failed (the gateway exited at start (exit 1))'),
  );
  assert.ok(!labels('starting').includes('Start daemon'));
  trayMenu('failed', '', actions).find((i) => i.label === 'Start daemon')!.click!();
  assert.equal(calls.pop(), 'start');
  assert.equal(trayIcon('stopped', '', actions, view(5), follow({ state: 'available', next: 'b' }), mode()), 'trouble'); // trouble outranks the rest
});

test('connect mode: an unreachable daemon shows the warning and Reconnect; a daemon that answers shows nothing', () => {
  const down = mode({
    mode: 'connect',
    link: { status: 'cannot-pair', address: 'x:7443', version: null, reason: 'timed out', mismatch: () => null },
  });
  const l = labels('running', '', undefined, down);
  assert.ok(l.includes('⚠ Cannot reach the daemon: timed out'));
  trayMenu('running', '', actions, undefined, down).find((i) => i.label === 'Reconnect')!.click!();
  assert.equal(calls.pop(), 'reconnect');
  assert.equal(trayIcon('running', '', actions, view(), undefined, down), 'trouble');
  const up = mode({ mode: 'connect' });
  assert.ok(!labels('running', '', undefined, up).some((x) => x.startsWith('⚠')));
});

test('Option shows the mode, the daemon, the commit and the switches; the address and the commit copy on a click', () => {
  const l = labels('running', '', follow(), mode(), view(0, true));
  assert.ok(l.includes('Mode: Own daemon'));
  assert.ok(l.includes('Daemon: 127.0.0.1:7443 · abc1234'));
  assert.ok(l.includes('Handline is on main aaa1111'));
  assert.ok(l.includes('Check for update'));
  assert.ok(l.includes('Switch to Connect'));
  const items = trayMenu('running', '', actions, follow(), mode(), view(0, true));
  items.find((i) => i.click && i.label?.startsWith('Daemon:'))!.click!();
  items.find((i) => i.label?.startsWith('Handline is on main'))!.click!();
  assert.deepEqual(calls.splice(0), ['copy 127.0.0.1:7443', 'copy aaa1111ffff']);
  assert.ok(!labels('running', '', follow(), mode(), view()).includes('Check for update'));
});

test('a daemon that is not the app build is told in the menu, with Restart daemon when this app can move it', () => {
  const link = {
    status: 'connected' as const,
    address: 'a',
    version: '0000000',
    reason: '',
    mismatch: () => 'Daemon is 0000000, app is abc123d',
  };
  assert.deepEqual(labels('running', '', undefined, mode({ link, restartDaemon: () => calls.push('restart-daemon') })).slice(1, 4), [
    '-',
    '⚠ Daemon is 0000000, app is abc123d',
    'Restart daemon',
  ]);
  assert.ok(!labels('running', '', undefined, mode({ link })).includes('Restart daemon')); // another device's daemon
  assert.ok(!labels('running', '', undefined, mode()).some((l) => l.includes('Daemon is'))); // matching builds say nothing
});
