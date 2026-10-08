// The operation id of a message belongs to the message, not its text: each send makes one, and a retry of a failed row sends the
// row's own. Two messages with the same text are two operations.
import type { Receipt } from '@handline/client';

export const newOperationId = () => `op-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

export type Delivery = 'now' | 'queue';

/** The operation of each row a send put in the cache, by the row's provisional id. A retry repeats its params, delivery included. */
const ops = new Map<number, { op: string; delivery?: Delivery }>();
export const rememberOperation = (row: number, op: string, delivery?: Delivery) => void ops.set(row, { op, delivery });
export const operationOf = (row: number) => ops.get(row);
export const forgetOperation = (op: string) => {
  for (const [row, x] of ops) if (x.op === op) ops.delete(row);
};
export const forgetRow = (row: number) => void ops.delete(row);

const RANK: Record<Receipt['state'], number> = { queued: 0, sent: 1, confirmed: 2, unknown: 2 };

/** The receipt to keep: a stale reply never takes a message back from where an event already put it. Unknown words count as sent. */
export function laterReceipt(have: Receipt | undefined, got: Receipt | undefined): Receipt | undefined {
  if (!have || !got) return got ?? have;
  const r = (x: Receipt) => RANK[x.state] ?? 1;
  return r(got) > r(have) ? got : have;
}
