// What contract/fixtures/delivery.json needs of a daemon: two more structured sessions, one with a turn running and one whose last
// message was lost after it was written. `node src/serve.ts --conformance` serves them; the vars are
//   transcript_agent=handline.auditor working_agent=conf.working crashed_agent=conf.crashed terminal_agent=handline.term-build
import { DaemonClient, memoryPair } from '@handline/client';
import type { FakeDaemon } from './daemon.ts';
import { AGENTS } from './fixtures.ts';
import { newSession } from './session.ts';

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
