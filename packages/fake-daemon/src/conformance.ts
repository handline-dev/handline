// The agents, accounts and tasks the conformance fixtures need of a daemon, served by `node src/serve.ts --conformance`.
// contract/README.md names, per fixture file, the var values that point at them.
import { DaemonClient, memoryPair } from '@handline/client';
import type { FakeDaemon } from '#src/daemon.ts';
import { AGENTS } from '#src/fixtures.ts';
import { newSession } from '#src/session.ts';

export async function seedConformance(daemon: FakeDaemon): Promise<void> {
  const base = AGENTS.find((a) => a.name === 'handline.auditor')!;
  for (const [name, state] of [
    ['conf.working', 'running'],
    ['conf.crashed', 'idle'],
  ] as const) {
    daemon.sessions.set(name, newSession({ ...base, name, state }, new Date().toISOString()));
    daemon.transcript.structure(name);
    if (state === 'running') daemon.chat.setTurn(name, true);
  }
  daemon.accounts.add({ name: 'conf.login', login_state: 'needs_login' });
  const wall = { account: 'alpha', clock: 1_790_000_000, assumed: true };
  for (const [name, state, view] of [
    ['view.ready', 'idle', { kind: 'opus high', task_count: 2, ready: true, wall: null }],
    ['view.blocked', 'idle', { kind: 'opus high', task_count: 1, ready: false, wall: null }],
    [
      'view.busy',
      'running',
      {
        kind: 'codex gpt-5 mid',
        task_count: 1,
        ready: false,
        wall: null,
        attention: 'running',
        doing: { text: 'Bash sleep 30', at: new Date().toISOString() },
      },
    ],
    ['view.walled', 'closed', { kind: 'opus max', task_count: 0, ready: false, wall }],
    [
      'view.plain',
      'closed',
      { kind: 'opus max', task_count: 0, ready: false, wall: null, fire: null, attention: null, cache_ignored: false, config_error: null },
    ],
    ['view.fire', 'idle', { kind: 'opus high', fire: 'context' }],
    ['view.ask', 'idle', { kind: 'opus high', attention: 'ask' }],
    ['view.alert', 'idle', { kind: 'opus high', attention: 'alert' }],
    ['view.stuck', 'idle', { kind: 'opus high', attention: 'stuck' }],
    ['view.ignored', 'idle', { kind: 'opus high', cache_ignored: true }],
    ['view.broken', 'closed', { config_error: 'agent.yaml: invalid YAML' }],
  ] as const) {
    daemon.sessions.set(name, newSession({ ...base, name, state, view }, new Date().toISOString()));
  }
  daemon.sessions.set('conf.output', newSession({ ...base, name: 'conf.output' }, new Date().toISOString()));
  daemon.transcript.structure('conf.output');
  daemon.chat.seedItem('conf.output', { kind: 'tool', tool: 'Bash', text: "printf 'a\\nb\\n'", output: 'a\nb\n' });
  daemon.chat.seedItem('conf.output', { kind: 'tool', tool: 'Bash', text: 'seq 1 10', output: '1\n2\n', dropped_lines: 8 });
  daemon.chat.seedItem('conf.output', { kind: 'tool', tool: 'Bash', text: 'true' });
  for (const name of ['conf.reply', 'conf.reply2']) {
    daemon.sessions.set(name, newSession({ ...base, name }, new Date().toISOString()));
    daemon.transcript.structure(name);
    daemon.chat.seedItem(name, { kind: 'tool', tool: 'Read', text: 'src/main.ts' });
    daemon.chat.seedItem(name, { kind: 'reply', text: `Read it; ${name} has one question.` });
  }
  const at = new Date().toISOString();
  const clock = { length_s: 3600, default: false, start: at, end: at, overdue_since: null, told: 0, left_s: null };
  const task = (id: number, to: string, state: string, more: object = {}) =>
    daemon.tasks.seed({ id, from: 'handline.architect', to, title: `task ${id}`, state, clock, ...more });
  task(9011, 't.blocked', 'doing');
  task(9012, 't.blocked', 'queued', {
    blockers: [{ task: 9011, reason: 'needs it' }],
    block_reason: 'waiting on its blocker',
    clock: { ...clock, default: true, start: null, end: null },
  });
  task(9021, 't.timed', 'doing');
  task(9031, 't.overdue', 'doing', { clock: { ...clock, overdue_since: at, told: 1 } });
  task(9041, 't.done', 'done', { done: at });
  task(9051, 't.paused', 'doing', { clock: { ...clock, start: null, end: null, left_s: 1200 } });
  const client = new DaemonClient({
    client: 'conformance-seed',
    connect: async () => {
      const [a, b] = memoryPair();
      daemon.accept(b);
      return a;
    },
  });
  await client.start();
  // sent through the wire, so a repeat of this request_id is answered from the daemon's kept replies
  await client.call('chat.send', { agent: 'conf.crashed', body: 'in flight when it died', request_id: 'crashed-op' });
  daemon.chat.lose('conf.crashed');
  client.close();
}
