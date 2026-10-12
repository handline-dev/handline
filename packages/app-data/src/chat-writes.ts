// The three chat writes as cache edits. A send shows its row at once and, if refused, keeps it as `failed` with its text, so nothing typed is lost;
// dismiss and answer change the message and are put back if refused.
import type { ChatMessage, Receipt } from '@handline/client';
import { knownAttachments } from './attachments.ts';
import { classify, sentence } from './errors.ts';
import { edit, mapPages, provisionalId, type Ctx, type Pages } from './cache.ts';
import { qk } from './reads.ts';
import { forgetOperation, laterReceipt, rememberOperation } from './send-ops.ts';
import { patchMessage, type MsgPage } from './chat-events.ts';
import type { WriteSpec } from './write-spec.ts';

const allChats = (d: string) => [['d', d, 'chat-messages'] as const];

export function waitingMessage(me: string, agent: string, body: string, now: string, parent: number | null = null, attachments: string[] = []): ChatMessage {
  return {
    id: provisionalId(),
    at: now,
    state_at: now,
    from: me,
    to: agent,
    state: 'queued',
    landing: 'queued',
    sending_reason: null,
    body,
    answer: null,
    dismissed: false,
    delivered_at: null,
    typed_at: null,
    landed_at: null,
    parent,
    ...(attachments.length ? { attachments: knownAttachments(attachments) } : {}),
    kind: 'message',
    tasks: [],
    is_task_question: false,
  };
}

const editAll = (c: Ctx, f: (d: Pages<MsgPage>) => Pages<MsgPage>) => edit<Pages<MsgPage>>(c.qc, ['d', c.d, 'chat-messages'], f);
const setRow = (c: Ctx, id: number, patch: Partial<ChatMessage>) => editAll(c, (d) => patchMessage(d, id, patch));

/** The row a send shows is told apart from the daemon's by its negative id; keyed by the send's own text and agent. */
const sendParams = (p: never) => p as { agent: string; body: string; parent?: number; attachments?: string[]; delivery?: 'now' | 'queue'; request_id?: string };

const findRow = (d: Pages<MsgPage>, id: number) => d.pages.flatMap((page) => page.messages).find((m) => m.id === id);
const setReceipt = (c: Ctx, id: number, receipt: Receipt | undefined) =>
  editAll(c, (d) => {
    const row = findRow(d, id);
    const next = laterReceipt(row?.receipt, receipt);
    return row && next && next !== row.receipt ? patchMessage(d, id, { receipt: next }) : d;
  });

export const chatWrites: Record<string, WriteSpec> = {
  // One chunk of an image; the chunks are the caller's loop (attachments.ts), and nothing in the cache changes until chat.send names the image.
  'attachment.put': { kind: 'decided', label: 'attach image' },
  'chat.send': {
    kind: 'authored',
    label: 'send',
    touches: allChats,
    patch: (c, p) => {
      const { agent, body, parent, attachments, delivery, request_id } = sendParams(p);
      const row = waitingMessage(c.me, agent, body, c.now, parent ?? null, attachments);
      if (request_id) rememberOperation(row.id, request_id, delivery);
      editAll(c, (d) => mapPages(d, (page, i) => (i === 0 ? { ...page, messages: [row, ...page.messages] } : page)));
    },
    // The row stays, said to have failed, with the daemon's sentence; Retry and Discard act on it.
    fail: (c, p, e) => {
      const { agent, body } = sendParams(p);
      editAll(c, (d) =>
        mapPages(d, (page) => ({
          ...page,
          messages: page.messages.map((m) =>
            m.id < 0 && m.to === agent && m.body === body && m.state !== 'failed'
              ? { ...m, state: 'failed', sending_reason: sentence(classify(e)) }
              : m,
          ),
        })),
      );
      return true;
    },
    // The daemon's number replaces the row's, unless its event already put the real message in.
    reconcile: (c, p, result) => {
      const { agent, body, request_id } = sendParams(p);
      const { request, receipt } = result as { request: number; receipt?: Receipt };
      if (request_id) forgetOperation(request_id);
      editAll(c, (d) => {
        const has = d.pages.some((page) => page.messages.some((m) => m.id === request));
        return mapPages(d, (page) => ({
          ...page,
          messages: page.messages.flatMap((m) =>
            m.id < 0 && m.to === agent && m.body === body && m.state !== 'failed' ? (has ? [] : [{ ...m, id: request }]) : [m],
          ),
        }));
      });
      setReceipt(c, request, receipt);
    },
  },
  // A queued message of a structured agent written now: its receipt is the daemon's, and the CLI's echo follows as an event.
  'chat.send_now': {
    kind: 'decided',
    label: 'send now',
    reconcile: (c, p, result) => setReceipt(c, (p as { request: number }).request, (result as { receipt?: Receipt }).receipt),
  },
  // Taking back a message that was only queued: it was never written, so it leaves the conversation (a written one is closed, and stays).
  'chat.cancel': {
    kind: 'decided',
    label: 'take back',
    reconcile: (c, p) => {
      const { request } = p as { request: number };
      editAll(c, (d) =>
        findRow(d, request)?.receipt?.state === 'queued'
          ? mapPages(d, (page) => ({ ...page, messages: page.messages.filter((m) => m.id !== request) }))
          : d,
      );
    },
  },
  'chat.dismiss': {
    kind: 'authored',
    label: 'dismiss',
    touches: (d) => [...allChats(d), qk.inboxes(d)],
    patch: (c, p) => {
      const { request } = p as { request: number };
      setRow(c, request, { landing: 'dismissed', state: 'dismissed', dismissed: true });
      // An inbox item is a request under the same id; a dismissed one leaves the mailbox.
      edit<{ items: { id: number }[] }>(c.qc, qk.inboxes(c.d), (l) => ({ ...l, items: l.items.filter((i) => i.id !== request) }));
    },
  },
  'chat.answer': {
    kind: 'authored',
    label: 'answer',
    touches: allChats,
    patch: (c, p) => {
      const { request, text } = p as { request: number; text: string };
      setRow(c, request, { landing: 'answered', state: 'answered', answer: { id: 0, text, at: c.now } });
    },
  },
};
