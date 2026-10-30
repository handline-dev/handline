import type { MethodName, Methods, RequestId } from '#src/protocol.ts';
import type { Transport } from '#src/transport.ts';

/** A call's params; the client fills in request_id on writes. */
export type CallParams<M extends MethodName> = Omit<Methods[M]['params'], 'request_id'> & { request_id?: RequestId };

export type HelloResult = Methods['hello']['result'];

export type ConnectionState = 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface ClientOptions {
  connect: () => Promise<Transport>;
  client: string; // who is calling; the daemon names this actor on what it does
  token?: string; // the device token from pairing; the network listener admits no one without it
  key?: () => string | null; // the founder key, read at each hello: the person's own app proves itself on the Unix socket with it
  founder?: boolean; // a hello not answered as the founder (no key yet) drops the connection to be retried, not kept at a lower role
  reconnect?: { minMs: number; maxMs: number } | false;
  newRequestId?: () => string;
  /** Methods answered by something other than the daemon connection (the desktop shell, for calls this caller may not make itself); each throws what a failed call throws. */
  relay?: { [M in MethodName]?: (params: CallParams<M>) => Promise<Methods[M]['result']> };
}
