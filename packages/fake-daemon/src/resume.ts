// Coming back to the stream: subscribe's params, and the cursor the reads a stream event keeps current were read at (the Go daemon's
// stream.Stamped), so a client that read one drops the events at or below it.
import type { Topic } from '@handline/contract';
import { badRequest } from '#src/errors.ts';

const STAMPED = new Set([
  'accounts.list',
  'agent.get',
  'agent.strip',
  'agent.timeline',
  'agents.list',
  'chat.messages',
  'chat.transcript',
  'daemon.status',
  'hr.list',
  'inbox.get',
  'inbox.list',
  'operation.get',
  'prefs.get',
  'prompt.list',
  'task.get',
  'task.time',
  'tasks.list',
]);

/** What read gives, with `cursor`: top, the head before the read, when the method is a stamped read. */
export const stamped = (method: string, top: number, read: () => unknown): unknown => {
  const result = read();
  return STAMPED.has(method) && result && typeof result === 'object' ? { ...result, cursor: top } : result;
};

/** subscribe's topics, once its params are checked: a non-empty list of served topics, and resume replay or latest. */
export const subscribeTopics = (p: Record<string, unknown>, unserved: Set<Topic>): Topic[] => {
  if (!Array.isArray(p.topics) || !p.topics.length) throw badRequest('topics must be a non-empty list', 'topics');
  for (const t of p.topics as Topic[]) if (unserved.has(t)) throw badRequest(`no such topic: ${t}`, 'topics');
  if (p.resume !== undefined && p.resume !== 'replay' && p.resume !== 'latest') throw badRequest('resume is replay or latest', 'resume');
  return p.topics as Topic[];
};
