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
