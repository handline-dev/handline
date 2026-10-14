import { test } from 'node:test';
import assert from 'node:assert/strict';
import { grant } from '../src/grant.ts';
import { commands, Owner, resolveClaude, type Child, type Cmd } from '../src/owner.ts';

const DAEMON: Cmd = { file: 'daemon', args: ['-structured-tick'] };
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
      if (over.neverUp !== cmd) setTimeout(() => (cmd === DAEMON ? (up.socket = true) : (up.gateway = true)), 0);
      return child;
    },
    daemon: DAEMON,
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
  assert.deepEqual(r.spawned, [DAEMON, GO]);
  assert.deepEqual(r.owner.owned(), ['daemon', 'gateway']);
  r.owner.stop();
  assert.deepEqual(r.killed, ['go', 'daemon']);
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
  const dies = rig({ socket: false, gateway: false }, { exitAtStart: DAEMON });
  await dies.owner.start();
  assert.equal(dies.owner.state, 'failed');
  assert.match(dies.owner.reason, /daemon exited at start/);
  assert.deepEqual(dies.spawned, [DAEMON]);

  const slow = rig({ socket: false, gateway: false }, { neverUp: DAEMON });
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
  q.exits['daemon']!(null);
  assert.equal(q.owner.state, 'stopped');
});

test('commands: a checkout runs go/handlined as the daemon and the gateway; the home and the binaries can be named', () => {
  const none = commands(() => undefined, '/repo', '127.0.0.1:7443');
  assert.deepEqual(none.daemon, {
    file: '/repo/go/handlined',
    args: ['-allow-real-home', '-structured-cli', 'claude', '-structured-tick', '-terminal-tick', '-cutover'],
  });
  assert.deepEqual(none.gateway.args, ['-gateway', '-allow-real-home', '-listen', '127.0.0.1:7443']);
  const named = commands((k) => ({ HOME: '/h', CLAUDE: '/x/claude', HANDLINED: '/x/h' })[k], '/repo', ':1');
  const ts = commands(() => undefined, '/repo', '100.101.2.3:7443', 'mac.tail1.ts.net:7443');
  assert.deepEqual(ts.gateway.args.slice(-4), ['-listen', '100.101.2.3:7443', '-advertise', 'mac.tail1.ts.net:7443']);
  assert.deepEqual(named.daemon.args.slice(0, 4), ['-home', '/h', '-structured-cli', '/x/claude']);
  assert.equal(named.daemon.file, '/x/h');
  assert.deepEqual(named.gateway, { file: '/x/h', args: ['-gateway', '-home', '/h', '-listen', ':1'] });
});

test('replaceGateway stops only the gateway, waits for its socket to go, and starts the new command', async () => {
  const GO2: Cmd = { file: 'go2', args: ['-listen', '127.0.0.1:1'] };
  const r = rig({ socket: false, gateway: false });
  await r.owner.start();
  r.spawned.length = 0;
  let up = true;
  // The old gateway's socket stays up until it exits: the test lets it go on kill.
  r.owner['d'].gatewayUp = async () => up;
  const realSpawn = r.owner['d'].spawn;
  r.owner['d'].spawn = (cmd) => {
    up = true;
    return realSpawn(cmd);
  };
  const stop = r.owner['started'].find((s) => s.name === 'gateway')!.child.kill;
  r.owner['started'].find((s) => s.name === 'gateway')!.child.kill = () => {
    stop();
    up = false;
  };
  await r.owner.replaceGateway(GO2);
  assert.deepEqual(r.killed, ['go']);
  assert.deepEqual(r.spawned, [GO2]);
  assert.equal(r.owner.state, 'running');
  assert.deepEqual(r.owner.owned(), ['daemon', 'gateway']);
});

test('fail shows a reason without touching what runs', async () => {
  const r = rig({ socket: true, gateway: true });
  await r.owner.start();
  r.owner.fail('the gateway does not answer');
  assert.equal(r.owner.state, 'failed');
  assert.equal(r.owner.reason, 'the gateway does not answer');
  assert.deepEqual(r.killed, []);
});

test('moveGateway leaves a gateway the app did not start alone, and keeps the old address when the new one does not answer', async () => {
  const GO2: Cmd = { file: 'go2', args: ['-listen', '127.0.0.1:1'] };
  const theirs = rig({ socket: true, gateway: true }); // another process answers on the gateway socket
  await theirs.owner.start();
  assert.equal(await theirs.owner.moveGateway(GO2, async () => true, 'no answer'), false);
  assert.deepEqual(theirs.killed, []);
  assert.deepEqual(theirs.spawned, []);
  assert.equal(theirs.owner.state, 'failed');
  assert.equal(theirs.owner.reason, 'no answer');

  const silent = rig({ socket: false, gateway: false });
  await silent.owner.start();
  let up = true;
  silent.owner['d'].gatewayUp = async () => up;
  silent.owner['started'].find((s) => s.name === 'gateway')!.child.kill = () => void (up = false);
  const spawn = silent.owner['d'].spawn;
  silent.owner['d'].spawn = (cmd) => ((up = true), spawn(cmd));
  assert.equal(await silent.owner.moveGateway(GO2, async () => false, 'still nothing'), false);
  assert.equal(silent.owner.reason, 'still nothing');
  assert.equal(await silent.owner.moveGateway(GO2, async () => true, 'x'), true); // the replacement is the app's own, and answers
  assert.equal(silent.owner.state, 'running');
});

test('a gateway that answers once and is gone a moment later is started, not trusted', async () => {
  const r = rig({ socket: true, gateway: true });
  let answers = 0;
  const spawned: Cmd[] = [];
  const up = { gateway: true };
  const owner = new Owner({
    socketUp: async () => true,
    gatewayUp: async () => (++answers === 1 ? true : up.gateway),
    spawn: (cmd) => {
      spawned.push(cmd);
      up.gateway = true;
      return { kill: () => undefined, onExit: () => undefined };
    },
    daemon: DAEMON,
    gateway: GO,
    wait: async () => {
      up.gateway = false; // the earlier run finished shutting down while the app waited
    },
    polls: 5,
  });
  await owner.start();
  assert.deepEqual(spawned, [GO]);
  assert.equal(owner.state, 'running');
  assert.deepEqual(r.spawned, []);
});

test('a gateway the app did not start is not moved, and the refusal is the failure the page is told', async () => {
  const r = rig({ socket: true, gateway: true });
  await r.owner.start();
  const moved = await r.owner.moveGateway(GO, async () => false, 'the gateway does not answer on 100.1.2.3:7443 (a firewall?)');
  assert.equal(moved, false);
  assert.deepEqual(r.killed, []);
  assert.deepEqual(grant(null, '100.1.2.3', r.owner), {
    token: null,
    ownHost: '100.1.2.3',
    failure: 'the gateway does not answer on 100.1.2.3:7443 (a firewall?)',
  });
});

test('a listener hears every failure, with its reason, also a later one', async () => {
  const r = rig({ socket: true, gateway: true });
  const seen: string[] = [];
  r.owner.onChange(() => r.owner.state === 'failed' && seen.push(r.owner.reason));
  await r.owner.start();
  r.owner.fail('the gateway stopped (exit 1)');
  assert.deepEqual(seen, ['the gateway stopped (exit 1)']);
});

test('resolveClaude: the override wins; else the first executable claude on the login PATH, then the app PATH; none is an error', () => {
  const have = new Set(['/home/u/.local/bin/claude', '/usr/bin/claude']);
  const isExec = (f: string) => have.has(f);
  assert.equal(resolveClaude('/x/claude', ['/usr/bin'], isExec), '/x/claude');
  assert.equal(resolveClaude(undefined, ['/home/u/.local/bin:/usr/bin', '/usr/bin'], isExec), '/home/u/.local/bin/claude');
  assert.equal(resolveClaude(undefined, [undefined, '/usr/bin'], isExec), '/usr/bin/claude');
  assert.throws(() => resolveClaude(undefined, ['/nowhere', undefined], isExec), /claude was not found/);
});

test('the daemon is resolved when it is spawned: a resolved path reaches the command, a missing claude fails with the reason and spawns nothing', async () => {
  const run = async (daemon: () => Promise<Cmd>) => {
    const spawned: Cmd[] = [];
    const owner = new Owner({
      socketUp: async () => spawned.length > 0,
      gatewayUp: async () => true,
      spawn: (cmd) => (spawned.push(cmd), { kill() {}, onExit() {} }),
      daemon,
      gateway: GO,
      wait: async () => undefined,
    });
    await owner.start();
    return { owner, spawned };
  };
  const ok = await run(async () => commands(() => undefined, '/repo', ':1', null, '/home/u/.local/bin/claude').daemon);
  assert.equal(ok.owner.state, 'running');
  assert.deepEqual(ok.spawned[0].args.slice(0, 2), ['-allow-real-home', '-structured-cli']);
  assert.equal(ok.spawned[0].args[2], '/home/u/.local/bin/claude');
  const none = await run(async () => resolveClaude(undefined, [], () => false) as never);
  assert.equal(none.owner.state, 'failed');
  assert.match(none.owner.reason, /claude was not found/);
  assert.equal(none.spawned.length, 0);
});
