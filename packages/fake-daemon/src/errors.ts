import type { ErrorCode } from '@handline/contract';

/** A refusal or error the caller gets as a JSON-RPC error. */
export class Failure extends Error {
  code: ErrorCode;
  data: Record<string, unknown>;
  constructor(code: ErrorCode, message: string, data: Record<string, unknown> = {}) {
    super(message);
    this.code = code;
    this.data = { code, ...data };
  }
}
export const refused = (gate: string, reason: string, hint?: string) => new Failure('refused', reason, { reason, gate, hint });
export const badRequest = (message: string, field?: string) => new Failure('bad_request', message, { field });

/** The daemon's error for a refusal of one of the fake stores (chat, tasks): they say `code` and, for a refusal, the `gate`. */
export const storeFailure = (e: { code: 'not_found' | 'refused' | 'bad_request'; message: string; gate?: string }) =>
  e.code === 'refused'
    ? refused(e.gate ?? 'state', e.message)
    : e.code === 'bad_request'
      ? badRequest(e.message)
      : new Failure('not_found', e.message);
