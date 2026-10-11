// What contract/fixtures/delivery.json needs of a daemon: two more structured sessions, one with a turn running and one whose last
// message was lost after it was written. `node src/serve.ts --conformance` serves them; the vars are
//   transcript_agent=handline.auditor working_agent=conf.working crashed_agent=conf.crashed terminal_agent=handline.term-build
// and, for structured_controls.json, structured_agent=handline.auditor (idle) turn_agent=conf.working interrupt_agent=conf.interrupt (its turn is ended by
// the fixture) asking_agent=conf.asking (a permission is open); and, for agent_view_fields.json, view_ready_agent=view.ready view_blocked_agent=view.blocked
// view_busy_agent=view.busy view_walled_agent=view.walled view_plain_agent=view.plain; and, for agent_view_attention.json, view_fire_agent=view.fire
// view_ask_agent=view.ask view_alert_agent=view.alert view_stuck_agent=view.stuck view_ignored_agent=view.ignored view_broken_agent=view.broken (plus busy and plain); and, for tasks_list_fields.json,
// tasks_blocked_owner=t.blocked tasks_timed_owner=t.timed tasks_overdue_owner=t.overdue tasks_done_owner=t.done; and, for transcript_output.json, output_agent=conf.output (three Bash tool items: one with output, one with output and dropped_lines 8, one with neither)
import { DaemonClient, memoryPair } from '@handline/client';
import type { FakeDaemon } from './daemon.ts';
import { AGENTS } from './fixtures.ts';
import { newSession } from './session.ts';

export async function seedConformance(daemon: FakeDaemon): Promise<void> {
  const base = AGENTS.find((a) => a.name === 'handline.auditor')!;
  for (const [name, state] of [
    ['conf.working', 'running'],
    ['conf.crashed', 'idle'],
    ['conf.interrupt', 'running'],
    ['conf.asking', 'running'],
  ] as const) {
    daemon.sessions.set(name, newSession({ ...base, name, state }, new Date().toISOString()));
    daemon.transcript.structure(name);
    if (state === 'running') daemon.chat.setTurn(name, true);
    if (name === 'conf.asking') daemon.transcript.ask(name, '6f1c0a52-0000-4000-8000-0000000000a1', 'Bash', 'ls');
  }
  const wall = { account: 'alpha', clock: 1_790_000_000, assumed: true };
  for (const [name, state, view] of [
    ['view.ready', 'idle', { kind: 'opus high', task_count: 2, ready: true, wall: null }],
    ['view.blocked', 'idle', { kind: 'opus high', task_count: 1, ready: false, wall: null }],
    ['view.busy', 'running', { kind: 'codex gpt-5 mid', task_count: 1, ready: false, wall: null, attention: 'running' }],
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
  const at = new Date().toISOString();
  const clock = { length_s: 3600, default: false, start: at, end: at, overdue_since: null, told: 0 };
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
