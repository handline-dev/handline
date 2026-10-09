// What contract/fixtures/delivery.json needs of a daemon: two more structured sessions, one with a turn running and one whose last
// message was lost after it was written. `node src/serve.ts --conformance` serves them; the vars are
//   transcript_agent=handline.auditor working_agent=conf.working crashed_agent=conf.crashed terminal_agent=handline.term-build
// and, for structured_controls.json, structured_agent=handline.auditor (idle) turn_agent=conf.working interrupt_agent=conf.interrupt (its turn is ended by
// the fixture) asking_agent=conf.asking (a permission is open); and, for agent_view_fields.json, view_ready_agent=view.ready view_blocked_agent=view.blocked
// view_busy_agent=view.busy view_walled_agent=view.walled view_plain_agent=view.plain
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
    ['view.busy', 'running', { kind: 'codex gpt-5 mid', task_count: 1, ready: false, wall: null }],
    ['view.walled', 'closed', { kind: 'opus max', task_count: 0, ready: false, wall }],
    ['view.plain', 'closed', { kind: 'opus max', task_count: 0, ready: false, wall: null }],
  ] as const) {
    daemon.sessions.set(name, newSession({ ...base, name, state, view }, new Date().toISOString()));
  }
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
