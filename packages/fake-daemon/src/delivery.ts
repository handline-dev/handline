// Delivery to a structured agent (x-delivery): the receipt of a message, and how landing follows it.
import { createHash } from 'node:crypto';
import type { ChatMessage, Landing, Receipt } from '@handline/contract';
import { badRequest } from './errors.ts';

export type Msg = ChatMessage & { agent: string };
type State = Receipt['state'];
const LANDING: Record<State, Landing> = { queued: 'queued', sent: 'typed', unknown: 'typed', confirmed: 'delivered' };

/** chat.send's `delivery`: now or queue (the default); another word is bad_request. */
export function deliveryOf(p: Record<string, unknown>): 'now' | 'queue' {
  if (p.delivery === undefined) return 'queue';
  if (p.delivery !== 'now' && p.delivery !== 'queue') throw badRequest('delivery is now or queue', 'delivery');
  return p.delivery;
}

/** A name-based uuid of the request_id: the same operation always gets the same one. */
export function uuidFor(requestId: string): string {
  const h = createHash('sha1').update(`handline-op:${requestId}`).digest('hex');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-5${h.slice(13, 16)}-8${h.slice(17, 20)}-${h.slice(20, 32)}`;
}

/** The message as it is when its receipt is `state`: landing follows the receipt. */
export function receiptPatch(m: Msg, state: State, at: string): Partial<ChatMessage> {
  const landing = LANDING[state];
  return {
    receipt: { ...m.receipt!, state },
    landing,
    state: landing,
    sending_reason: state === 'unknown' ? 'lost after it was written; it is not written again' : null,
    typed_at: state === 'queued' ? null : (m.typed_at ?? at),
    ...(state === 'confirmed' && { delivered_at: at, landed_at: at }),
  };
}
