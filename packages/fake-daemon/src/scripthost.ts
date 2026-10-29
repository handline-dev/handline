// What the scripted sessions reach of the daemon that hosts them: its clock, its events and the connections watching a terminal.
import type { Actor, Timestamp, Topic } from '@handline/contract';
import type { FakeChat } from '#src/chat.ts';
import type { Conn } from '#src/conn.ts';
import type { Host } from '#src/scriptcore.ts';
import { tid, type Session } from '#src/session.ts';
import { watcherNotice } from '#src/watchers.ts';

interface Hub {
  readonly sessions: Map<string, Session>;
  readonly conns: Set<Conn>;
  readonly chat: FakeChat;
  iso(): Timestamp;
  emit(topic: Topic, type: string, f?: { agent?: string; actor?: Actor; title?: string; data?: Record<string, unknown> }): void;
  notify(c: Conn, method: string, params: unknown): void;
}

export const scriptHost = (d: Hub): Host => ({
  iso: () => d.iso(),
  emit: (topic, type, f) => d.emit(topic, type, f),
  sessions: () => d.sessions,
  moved: (s, from) => d.chat.stateMoved(s.fx.name, from, s.state),
  broadcast: (s, method, params) => {
    for (const c of d.conns) {
      const a = c.attached.get(tid(s));
      const n = watcherNotice(a, method, params, s.cols);
      if (n) d.notify(c, method, n);
    }
  },
  broadcastBytes: (s, method, build) => {
    for (const c of d.conns) {
      const a = c.attached.get(tid(s));
      if (a?.format === 'bytes') d.notify(c, method, { ...build(), seq: ++a.seq });
    }
  },
  watched: (s) => [...d.conns].some((c) => c.attached.has(tid(s))),
});
