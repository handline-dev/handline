import type { ErrorCode } from './protocol.ts';

export class RpcError extends Error {
  // `transport` and `protocol` are the client's own: no connection, and a daemon it must not talk to.
  code: ErrorCode | 'transport' | 'protocol';
  data: Record<string, unknown> | undefined;
  constructor(code: ErrorCode | 'transport' | 'protocol', message: string, data?: Record<string, unknown>) {
    super(message);
    this.code = code; this.data = data;
  }
  /** The gate's own sentence, the command that fixes it, and which gate: from a `refused` error. */
  get reason(): string | undefined { return this.data?.reason as string | undefined; }
  get gate(): string | undefined { return this.data?.gate as string | undefined; }
  get hint(): string | undefined { return this.data?.hint as string | undefined; }
}

