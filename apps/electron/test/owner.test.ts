import { test } from 'node:test';
import assert from 'node:assert/strict';
import { commands, Owner, type Child, type Cmd } from '../src/owner.ts';

const PY: Cmd = { file: 'py', args: ['daemon'] };
const GO: Cmd = { file: 'go', args: ['-gateway'] };

function rig(up: { socket: boolean; gateway: boolean }, over: { exitAtStart?: Cmd; neverUp?: Cmd } = {}) {
  const spawned: Cmd[] = [];
  const killed: string[] = [];
  const exits: Record<string, (c: number | null) => void> = {};
  const owner = new Owner({
    socketUp: async () => up.socket,
    gatewayUp: async () => up.gateway,
    spawn: (cmd) => {
      spawned.push(cmd);
      const child: Child = {
        kill: () => killed.push(cmd.file),
        onExit: (f) => {
          exits[cmd.file] = f;
          if (over.exitAtStart === cmd) f(1);
        },
      };
      // A started child answers on its socket on the next poll, unless the test says it never does.
      if (over.neverUp !== cmd) setTimeout(() => (cmd === PY ? (up.socket = true) : (up.gateway = true)), 0);
      return child;
    },
    python: PY,
    gateway: GO,
    wait: () => new Promise((r) => setTimeout(r, 1)),
    polls: 5,
  });
  return { owner, spawned, killed, exits };
}

test('with nothing running it starts the daemon, then the gateway, and stops both on quit, gateway first', async () => {
  const r = rig({ socket: false, gateway: false });
  await r.owner.start();
  assert.equal(r.owner.state, 'running');
  assert.deepEqual(r.spawned, [PY, GO]);
  assert.deepEqual(r.owner.owned(), ['daemon', 'gateway']);
  r.owner.stop();
  assert.deepEqual(r.killed, ['go', 'py']);
  assert.equal(r.owner.state, 'stopped');
});

test('what is already running is not started again and not stopped', async () => {
  const r = rig({ socket: true, gateway: true });
  await r.owner.start();
  assert.equal(r.owner.state, 'running');
  assert.deepEqual(r.spawned, []);
  r.owner.stop();
  assert.deepEqual(r.killed, []);
});

test('only the missing half is started', async () => {
  const r = rig({ socket: true, gateway: false });
  await r.owner.start();
  assert.deepEqual(r.spawned, [GO]);
  r.owner.stop();
  assert.deepEqual(r.killed, ['go']);
});

test('a child that exits at start, or never answers, is a failure with a reason, and the gateway is not tried', async () => {
  const dies = rig({ socket: false, gateway: false }, { exitAtStart: PY });
  await dies.owner.start();
  assert.equal(dies.owner.state, 'failed');
  assert.match(dies.owner.reason, /daemon exited at start/);
  assert.deepEqual(dies.spawned, [PY]);

  const slow = rig({ socket: false, gateway: false }, { neverUp: PY });
  await slow.owner.start();
  assert.equal(slow.owner.state, 'failed');
  assert.match(slow.owner.reason, /did not answer/);
});

test('a child that dies after the app is up shows as failed, but not one stopped by quit', async () => {
  const r = rig({ socket: false, gateway: false });
  await r.owner.start();
  r.exits['go']!(137);
  assert.equal(r.owner.state, 'failed');
  assert.match(r.owner.reason, /gateway stopped \(exit 137\)/);

  const q = rig({ socket: false, gateway: false });
  await q.owner.start();
  q.owner.stop();
  q.exits['py']!(null);
  assert.equal(q.owner.state, 'stopped');
});

test('commands: a checkout runs bin/ocp and go/handlined; the home and the binaries can be named', () => {
  const none = commands(() => undefined, '/repo', '127.0.0.1:7443');
  assert.deepEqual(none.python, { file: '/repo/bin/ocp', args: ['daemon'] });
  assert.deepEqual(none.gateway.args, ['-gateway', '-allow-real-home', '-listen', '127.0.0.1:7443']);
  const named = commands((k) => ({ HOME: '/h', OCP: '/x/ocp', HANDLINED: '/x/h' })[k], '/repo', ':1');
  assert.equal(named.python.file, '/x/ocp');
  assert.deepEqual(named.gateway, { file: '/x/h', args: ['-gateway', '-home', '/h', '-listen', ':1'] });
});
