// A daemon error, sorted once into what a view should do about it (docs/turborepo-layout.md §8.3).
import { RpcError } from '@handline/client';

export type Failure =
  | { kind: 'transport' } // nothing in the view: the connection banner speaks and a reconnect refetches
  | { kind: 'not_found'; message: string } // the view's empty state
  | { kind: 'refused'; reason: string; hint?: string }
  | { kind: 'not_served' } // an unknown method, or gate `unsupported`
  | { kind: 'busy'; retryAfterMs: number }
  | { kind: 'protocol'; message: string } // the whole app's fatal sentence, not a view error
  | { kind: 'other'; message: string }; // an error block with Retry

export function classify(e: unknown): Failure {
  if (!(e instanceof RpcError)) return { kind: 'other', message: e instanceof Error ? e.message : String(e) };
  switch (e.code) {
    case 'transport':
      return { kind: 'transport' };
    case 'not_found':
      return { kind: 'not_found', message: e.message };
    case 'protocol':
      return { kind: 'protocol', message: e.reason ?? e.message };
    case 'daemon_busy':
      return { kind: 'busy', retryAfterMs: Number(e.data?.retry_after_ms ?? 1000) };
    case 'refused':
      return e.gate === 'unsupported' ? { kind: 'not_served' } : { kind: 'refused', reason: e.reason ?? e.message, hint: e.hint };
    case 'bad_request':
      return e.data?.method ? { kind: 'not_served' } : { kind: 'other', message: e.message };
    default:
      return { kind: 'other', message: e.message };
  }
}

/** The sentence a refusal or failure shows, with the gate's hint in brackets. */
export function sentence(f: Failure): string {
  switch (f.kind) {
    case 'refused':
      return f.hint ? `${f.reason} (${f.hint})` : f.reason;
    case 'not_found':
    case 'protocol':
    case 'other':
      return f.message;
    case 'busy':
      return 'The daemon is busy; trying again.';
    case 'not_served':
      return 'This daemon does not serve that.';
    case 'transport':
      return '';
  }
}

/** React Query's retry rule: only a busy daemon is asked again, a few times, after the wait it named. */
export const retryRead = (count: number, e: unknown) => count < 3 && classify(e).kind === 'busy';
export const retryDelay = (_count: number, e: unknown) => {
  const f = classify(e);
  return f.kind === 'busy' ? f.retryAfterMs : 1000;
};
