// Which area of the fake daemon answers a method: the first module that owns it. A new method goes in its area's file and, if it is a new
// area, in the list here; daemon.ts is not touched.
import type { Actor, Role, Topic } from '@handline/contract';
import type { FakeAccounts } from '#src/accounts.ts';
import type { FakeAdmin } from '#src/admin.ts';
import type { FakeAttachments } from '#src/attachments.ts';
import type { FakeChat } from '#src/chat.ts';
import type { Conn } from '#src/conn.ts';
import type { FakeInbox } from '#src/inbox.ts';
import type { Knobs } from '#src/knobs.ts';
import { invalidParams } from '#src/errors.ts';
import type { FakeRequests } from '#src/requests.ts';
import type { Script } from '#src/script.ts';
import { accountsReply, type Session } from '#src/session.ts';
import type { FakeTasks } from '#src/tasks.ts';
import { taskCall } from '#src/taskcall.ts';
import type { FakeWork } from '#src/workroom.ts';

/** What the areas need of the daemon that hosts them. */
export interface ModuleHost {
  readonly sessions: Map<string, Session>;
  readonly knobs: Knobs;
  readonly accounts: FakeAccounts;
  readonly admin: FakeAdmin;
  readonly work: FakeWork;
  readonly requests: FakeRequests;
  readonly uploads: FakeAttachments;
  readonly inbox: FakeInbox;
  readonly tasks: FakeTasks;
  readonly chat: FakeChat;
  readonly script: Script;
  emit(topic: Topic, type: string, f?: { data?: Record<string, unknown> }): void;
  session(agent: unknown): Session;
  actor(c: Conn): Actor;
}

type Ok = (said: string, extra?: object) => object;
type Area = (d: ModuleHost, c: Conn, method: string, p: Record<string, unknown>, ok: Ok) => { value: unknown } | null;

const AREAS: Area[] = [
  (d, _c, method, p, ok) => {
    if (method === 'accounts.list' && d.knobs.accounts) return { value: accountsReply(d.knobs.accounts, [...d.sessions.values()]) };
    return /^accounts?\./.test(method) ? { value: d.accounts.call(method, p, ok) } : null;
  },
  (d, _c, method, p, ok) => {
    if (!d.admin.owns(method)) return null;
    const value = d.admin.call(method, p, ok);
    if (method === 'device.revoke') d.emit('devices', 'device.revoked', { data: { device: p.device } });
    return { value };
  },
  (d, _c, method, p, ok) => (d.work.owns(method) ? { value: d.work.call(method, p, ok) } : null),
  (d, _c, method, p, ok) => (d.requests.owns(method) ? { value: d.requests.call(method, p, ok) } : null),
  (d, _c, method, p, ok) => (method === 'attachment.remove' ? { value: removeAttachment(d, p, ok) } : null),
  (d, _c, method, p, ok) => (method.startsWith('attachment.') ? { value: d.uploads.call(method, p, ok) } : null),
  (d, c, method, p, ok) => (method.startsWith('inbox.') ? { value: d.inbox.call(method, p, c.caller.name, ok) } : null),
  (d, c, method, p) => (d.tasks.serves(method) ? { value: taskOf(d, c, method, p) } : null),
];

/** attachment.remove: the count of messages, briefs and memos naming the image, then, unless a dry run, the removal everywhere. */
function removeAttachment(d: ModuleHost, p: Record<string, unknown>, ok: Ok): object {
  const id = String(p.id);
  if (p.dry_run !== undefined && typeof p.dry_run !== 'boolean') throw invalidParams('dry_run is true or false', 'dry_run');
  const dry = p.dry_run === true;
  if (!d.uploads.remove(p.id, dry)) return ok('It was removed already.', { removed_from: 0 });
  const n = d.chat.naming(id) + d.tasks.naming(id);
  if (dry) return ok(`Deletes it from ${n} place${n === 1 ? '' : 's'}.`, { removed_from: n });
  d.chat.remark((x) => d.uploads.marked(x));
  d.tasks.remark((x) => d.uploads.marked(x));
  for (const topic of ['chat', 'tasks'] as const) d.emit(topic, 'attachment.removed', { data: { id } });
  return ok(`Deleted from ${n} place${n === 1 ? '' : 's'}.`, { removed_from: n });
}

function taskOf(d: ModuleHost, c: Conn, method: string, p: Record<string, unknown>): unknown {
  const askAgent = (task: number, to: string, text: string) => {
    const s = d.session(to);
    if (!s.open) d.script.startSession(s, false, d.actor(c));
    const body =
      `Question on T${task} from the founder (the task keeps its holder): ${text}\n\n` +
      "Answer by replying to this message: hl say human --parent <this message's #id> '<answer>'";
    const { request } = d.chat.send({ agent: to, body, request_id: p.request_id }, c.caller.name);
    d.chat.link(request, { id: task, title: d.tasks.find(task).title });
    return request;
  };
  return taskCall(d.tasks, method, p, c.caller.name, c.role === ('agent' satisfies Role), d.script, askAgent);
}

/** The area's answer to the method; null for a method none of them owns. */
export function moduleCall(d: ModuleHost, c: Conn, method: string, p: Record<string, unknown>, ok: Ok): { value: unknown } | null {
  for (const area of AREAS) {
    const answer = area(d, c, method, p, ok);
    if (answer) return answer;
  }
  return null;
}
