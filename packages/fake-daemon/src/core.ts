// The fake daemon's own methods: the handshake, who is calling, the subscription, the status and the agents, pairing, pull requests.
import { METHODS, PROTOCOL, ROLE_ORDER, type MethodName, type Role, type Timestamp, type Topic } from '@handline/contract';
import type { FakeAdmin } from '#src/admin.ts';
import { TERMINAL_CAPS, TEXT_CAPS, type Conn } from '#src/conn.ts';
import { badRequest, refused } from '#src/errors.ts';
import type { EventLog } from '#src/eventlog.ts';
import type { FakeInbox } from '#src/inbox.ts';
import type { FakePairing } from '#src/pairing.ts';
import { subscribeTopics } from '#src/resume.ts';
import { detail, statusReply, view, type Session } from '#src/session.ts';
import type { FakeTranscript } from '#src/transcript.ts';

/** What these methods need of the daemon that hosts them. */
export interface CoreHost {
  readonly sessions: Map<string, Session>;
  readonly inbox: FakeInbox;
  readonly admin: FakeAdmin;
  readonly transcript: FakeTranscript;
  readonly pairing: FakePairing;
  readonly log: EventLog;
  readonly pullRequests: Set<number>;
  readonly unservedTopics: Set<Topic>;
  readonly serveChat: boolean;
  readonly serveBytes: boolean;
  readonly deviceRole: Role;
  readonly now: () => Date;
  iso(): Timestamp;
  /** Everything that may leave a method out of what this daemon lists and serves. */
  readonly served: { lists(name: string): boolean }[];
}

export const mayCall = (role: Role, lowest: Role): boolean => ROLE_ORDER.indexOf(role) >= ROLE_ORDER.indexOf(lowest);

export function hello(d: CoreHost, c: Conn, p: Record<string, unknown>): unknown {
  if (typeof p.client !== 'string') throw badRequest('hello needs client', 'client');
  if (typeof p.protocol !== 'number') throw badRequest('hello needs protocol', 'protocol');
  const device = c.network ? d.pairing.device(p.token) : undefined;
  if (device) {
    c.role = d.deviceRole;
    c.paired = true;
    c.caller = { kind: 'device', device: device.id, name: device.name };
  }
  c.helloed = true;
  if (c.caller.name === 'unknown') c.caller = { ...c.caller, name: p.client };
  return {
    protocol: PROTOCOL,
    min_client: 1,
    daemon: 'fake0000',
    schema: 1,
    role: c.role,
    caller: c.caller,
    capabilities: {
      chat: d.serveChat,
      transcript: d.transcript.on,
      review: false,
      ...(d.serveBytes ? TERMINAL_CAPS : TEXT_CAPS),
    },
    transport: c.network ? 'network' : 'local',
    methods: Object.entries(METHODS)
      .filter(([name]) => d.served.every((m) => m.lists(name)))
      .map(([name, m]) => ({ name, since: m.since, role: m.role, allowed: mayCall(c.role, m.role) })),
  };
}

/** The methods that belong to no area; refused as unsupported when it is none of them. */
export function coreCall(
  d: CoreHost,
  c: Conn,
  method: string,
  p: Record<string, unknown>,
  ok: (said: string, extra?: object) => object,
  agent: () => Session,
): unknown {
  switch (method as MethodName) {
    case 'whoami':
      return {
        caller: c.caller,
        role: c.role,
        methods: Object.entries(METHODS)
          .filter(([, m]) => mayCall(c.role, m.role))
          .map(([n]) => n),
      };
    case 'daemon.status':
      return statusReply(d.sessions, d.inbox.unread(), d.iso(), d.admin.machineNeeds);
    case 'agents.list':
      return { agents: [...d.sessions.values()].map((s) => view(s)) };
    case 'agent.get':
      return detail(agent());
    case 'pr.exists': {
      const repo = agent().fx.repo ?? null;
      const nums = p.numbers;
      if (!Array.isArray(nums) || nums.length > 20 || !nums.every((n) => Number.isInteger(n)))
        throw badRequest('numbers must be at most 20 integers', 'numbers');
      return { repo, found: repo ? (nums as number[]).filter((n) => d.pullRequests.has(n)) : [] };
    }
    case 'subscribe': {
      c.topics = new Set(d.transcript.gate(subscribeTopics(p, d.unservedTopics), c.role));
      return { cursor: d.log.head };
    }
    case 'unsubscribe':
      for (const t of (p.topics as Topic[] | undefined) ?? []) c.topics?.delete(t);
      return {};
    case 'prompt.list': {
      const s = agent();
      return { prompts: s.prompt ? [s.prompt] : [] };
    }
    case 'device.pair':
      return d.pairing.pair(ok, d.now());
    case 'device.redeem':
      return d.pairing.redeem(ok, p);
    default:
      throw refused('unsupported', `the fake daemon does not serve ${method}`);
  }
}
