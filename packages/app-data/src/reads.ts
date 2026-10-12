// Every read method of the contract, once: what its cache entry is called and what keeps it fresh. A hook never builds a key by hand.
import type { MethodName, Topic } from '@handline/client';

export type Key = readonly unknown[];

export interface ReadSpec {
  /** The second part of the key after the daemon id. */
  resource: string;
  /** Picks the one param that names the entry (`agent.get` by agent); without it the whole params object is part of the key. */
  id?: (p: never) => unknown;
  /** The topic whose events keep this entry current; without one the entry is a snapshot and goes stale after 30 s. */
  feed?: Topic;
}

const by = (name: string) => (p: never) => (p as Record<string, unknown>)[name];

export const reads = {
  'daemon.status': { resource: 'status', feed: 'status' },
  'agents.list': { resource: 'agents', feed: 'agents' },
  'agent.get': { resource: 'agent', id: by('agent'), feed: 'agents' },
  'agent.timeline': { resource: 'timeline', feed: 'agents' },
  'agent.options': { resource: 'agent-options' },
  'agent.triggers': { resource: 'triggers' },
  'prompt.list': { resource: 'prompts', id: by('agent'), feed: 'notifications' },
  'chat.messages': { resource: 'chat-messages', feed: 'chat' },
  'agent.strip': { resource: 'strip', id: by('agent'), feed: 'agents' },
  'chat.requests': { resource: 'chat-requests' },
  'chat.refs': { resource: 'chat-refs', id: by('agent') },
  'chat.transcript': { resource: 'chat-transcript', id: by('agent'), feed: 'chat' },
  'transcript.events': { resource: 'transcript-events', id: by('agent'), feed: 'transcript' },
  'chat.locate': { resource: 'chat-locate' },
  'chat.search': { resource: 'chat-search' },
  'inbox.list': { resource: 'inbox', feed: 'inbox' },
  'inbox.get': { resource: 'inbox-item', id: by('id'), feed: 'inbox' },
  'tasks.list': { resource: 'tasks', feed: 'tasks' },
  'task.get': { resource: 'task', id: by('task'), feed: 'tasks' },
  'task.memo.list': { resource: 'memos', id: by('task') },
  'task.board': { resource: 'board', feed: 'tasks' },
  'trust.list': { resource: 'trust' },
  'memory.status': { resource: 'memory' },
  'team.get': { resource: 'team' },
  'hr.list': { resource: 'hr', feed: 'hr' },
  'accounts.list': { resource: 'accounts', feed: 'accounts' },
  'repos.list': { resource: 'repos' },
  'worktrees.list': { resource: 'worktrees' },
  'worktree.diff': { resource: 'worktree-diff' },
  'schedules.list': { resource: 'schedules' },
  'stats.get': { resource: 'stats' },
  'stats.outlook': { resource: 'outlook' },
  'settings.get': { resource: 'settings' },
  'answers.list': { resource: 'answers' },
  'devices.list': { resource: 'devices', feed: 'devices' },
  'operation.get': { resource: 'operation', id: by('operation'), feed: 'operations' },
} as const satisfies Partial<Record<MethodName, ReadSpec>>;

/** Reads that are not cache entries: the handshake, the subscription, the event log (the bridge reads events itself), the terminal stream, and the agent's own progress note (`hl progress`; no screen draws it). */
export const NOT_QUERIES = [
  'hello',
  'whoami',
  'subscribe',
  'unsubscribe',
  'events.feed',
  'progress.get',
  'terminal.attach',
  'terminal.history',
  'terminal.detach',
  // Reads no screen draws yet: the terminal's address (member) and the next account pick (agent).
  'terminal.open',
  'status.pick',
  // No screen draws an attachment or a task screenshot yet.
  'attachment.get',
  'task.shot',
] as const;

export type ReadMethod = keyof typeof reads;

const hasParams = (p: unknown) => !!p && Object.values(p).some((v) => v !== undefined);

/** The cache key of one read: `['d', daemon, resource, id-or-params?]`. */
export function keyOf(daemon: string, method: ReadMethod, params?: unknown): Key {
  const spec: ReadSpec = reads[method];
  const base = ['d', daemon, spec.resource] as const;
  if (spec.id) return [...base, spec.id(params as never)];
  return hasParams(params) ? [...base, params] : base;
}

/** The named keys the event bridge and the writes patch. */
export const qk = {
  agents: (d: string) => keyOf(d, 'agents.list'),
  agent: (d: string, agent: string) => keyOf(d, 'agent.get', { agent }),
  status: (d: string) => keyOf(d, 'daemon.status'),
  accounts: (d: string) => keyOf(d, 'accounts.list'),
  /** Every inbox list, whatever its filter. */
  inboxes: (d: string) => ['d', d, reads['inbox.list'].resource] as const,
  inbox: (d: string, filter?: object) => keyOf(d, 'inbox.list', filter),
  /** Every hr list, whatever its filter. */
  hrs: (d: string) => ['d', d, reads['hr.list'].resource] as const,
  tasks: (d: string) => ['d', d, reads['tasks.list'].resource] as const,
  task: (d: string, task: number) => keyOf(d, 'task.get', { task }),
  memos: (d: string, task: number) => keyOf(d, 'task.memo.list', { task }),
  prompts: (d: string, agent: string) => keyOf(d, 'prompt.list', { agent }),
  operation: (d: string, operation: string) => keyOf(d, 'operation.get', { operation }),
  chatMessagesOf: (d: string, agent: string) => ['d', d, 'chat-messages', agent] as const,
  chatMessages: (d: string, agent: string, scope: string) => ['d', d, 'chat-messages', agent, scope] as const,
  transcript: (d: string, agent: string) => keyOf(d, 'chat.transcript', { agent }),
  transcriptEvents: (d: string, agent: string) => keyOf(d, 'transcript.events', { agent }),
  /** Everything of this daemon, for a gap or a reconnect. */
  all: (d: string) => ['d', d] as const,
  resource: (d: string, method: ReadMethod) => ['d', d, reads[method].resource] as const,
};
