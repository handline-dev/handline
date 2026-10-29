// The fake daemon's chat methods: one call checked and answered by the FakeChat store.
import { invalidParams, refused, storeFailure } from '#src/errors.ts';
import type { Attachment, ImagePart, TextPart } from '@handline/contract';
import { ChatFailure } from '#src/chatstore.ts';
import type { FakeChat } from '#src/chat.ts';

/** Runs a chat call and gives its failure as the JSON-RPC error the daemon sends. */
export function chatCall<T>(fn: () => T): T {
  try {
    return fn();
  } catch (e) {
    if (!(e instanceof ChatFailure)) throw e;
    throw storeFailure(e);
  }
}

/** What a message carries: FakeAttachments.message. */
type Images = (body: string, ids: unknown, task: unknown) => { attachments: Attachment[]; parts?: (TextPart | ImagePart)[] };

/** One chat method. `checkAgent` throws for an agent that does not exist. */
export function chatMethod(
  chat: FakeChat,
  method: string,
  p: Record<string, unknown>,
  person: string,
  checkAgent: () => unknown,
  ok: (said: string, extra?: object) => object,
  attachments: { message: Images } = { message: () => ({ attachments: [] }) },
): unknown {
  switch (method) {
    case 'chat.messages':
      checkRead(p, true);
      checkAgent();
      return chat.messagesOf(p);
    case 'chat.transcript':
      checkRead(p, false);
      checkAgent();
      return chat.transcriptOf(p);
    case 'chat.requests':
      checkRead(p, true);
      checkAgent();
      throw refused('unsupported', `the fake daemon does not serve ${method}`);
    case 'chat.send':
      checkAgent();
      return chatCall(() => ok('Sent.', chat.send(p, person, attachments.message)));
    case 'chat.send_now':
      return chatCall(() => ok('Typed.', chat.sendNow(p)));
    case 'chat.cancel':
      return chatCall(() => (chat.cancel(p), ok('Taken back.')));
    case 'chat.seen':
      return chatCall(() => (chat.seen(p), ok('Seen.')));
    case 'chat.locate':
      return chatCall(() => chat.locate(p));
    case 'chat.search':
      return chat.search(p);
    case 'chat.refs':
      if (typeof p.agent !== 'string' || !p.agent) throw invalidParams('agent is an agent name', 'agent');
      checkAgent();
      return chat.refs(p);
    default:
      throw refused('unsupported', `the fake daemon does not serve ${method}`);
  }
}

/** The limit of a page: a whole number from 1 to `max`, else bad_request naming `limit`. Absent is fine. */
function checkLimit(limit: unknown, max: number): void {
  if (limit !== undefined && !(Number.isInteger(limit) && (limit as number) >= 1 && (limit as number) <= max))
    throw invalidParams(`limit is a whole number from 1 to ${max}`, 'limit');
}

/** The params a chat read checks before it looks for the agent, in the daemon's order: scope, since with before, limit. */
function checkRead(p: Record<string, unknown>, scoped: boolean): void {
  if (scoped && p.scope !== 'mine' && p.scope !== 'all') throw invalidParams('scope is mine or all', 'scope');
  if (p.before !== undefined && p.since !== undefined) throw invalidParams('before and since are one or the other', 'since');
  checkLimit(p.limit, 200);
}
