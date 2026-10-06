// The four chat writes as cache edits. A send shows its row at once and, if refused, keeps it as `failed` with its text, so nothing typed is lost;
// withdraw, dismiss and answer change the message and are put back if refused.
import type { ChatMessage } from '@handline/client';
import { classify, sentence } from './errors.ts';
import { edit, mapPages, provisionalId, type Ctx, type Pages } from './cache.ts';
import { patchMessage, type MsgPage } from './chat-events.ts';
import type { WriteSpec } from './write-spec.ts';

const allChats = (d: string) => [['d', d, 'chat-messages'] as const];

export function waitingMessage(me: string, agent: string, body: string, now: string): ChatMessage {
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
    parent: null,
    kind: 'message',
    tasks: [],
    is_task_question: false,
  };
}

const editAll = (c: Ctx, f: (d: Pages<MsgPage>) => Pages<MsgPage>) => edit<Pages<MsgPage>>(c.qc, ['d', c.d, 'chat-messages'], f);
const setRow = (c: Ctx, id: number, patch: Partial<ChatMessage>) => editAll(c, (d) => patchMessage(d, id, patch));

/** The row a send shows is told apart from the daemon's by its negative id; keyed by the send's own text and agent. */
const sendParams = (p: never) => p as { agent: string; body: string };

export const chatWrites: Record<string, WriteSpec> = {
  'chat.send': {
    kind: 'authored',
    label: 'send',
    touches: allChats,
    patch: (c, p) => {
      const { agent, body } = sendParams(p);
      const row = waitingMessage(c.me, agent, body, c.now);
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
      const { agent, body } = sendParams(p);
      const request = (result as { request: number }).request;
      editAll(c, (d) => {
        const has = d.pages.some((page) => page.messages.some((m) => m.id === request));
        return mapPages(d, (page) => ({
          ...page,
          messages: page.messages.flatMap((m) =>
            m.id < 0 && m.to === agent && m.body === body && m.state !== 'failed' ? (has ? [] : [{ ...m, id: request }]) : [m],
          ),
        }));
      });
    },
  },
  'chat.withdraw': {
    kind: 'authored',
    label: 'withdraw',
    touches: allChats,
    patch: (c, p) => setRow(c, (p as { request: number }).request, { landing: 'withdrawn', state: 'withdrawn' }),
  },
  'chat.dismiss': {
    kind: 'authored',
    label: 'dismiss',
    touches: allChats,
    patch: (c, p) => setRow(c, (p as { request: number }).request, { landing: 'dismissed', state: 'dismissed', dismissed: true }),
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
