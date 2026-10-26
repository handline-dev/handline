// What the fake daemon's connections and options are made of: one connection's state, the options a test passes, and the constants the
// hello reply and the replay of a write read.
import { RPC_CODES, type Caller, type Role, type Topic } from '@handline/contract';
import type { Transport } from '@handline/client';
import type { Attach } from '#src/session.ts';
import type { Failure } from '#src/errors.ts';

export const TERMINAL_CAPS = { terminal_format: ['text', 'bytes', 'styled'], terminal_hosts: { fake: ['text', 'bytes', 'styled'] } };
export const TEXT_CAPS = { terminal_format: ['text'], terminal_hosts: { fake: ['text'] } };

// Writes whose reply is never replayed: a retry runs again and is judged on its own (a token is handed out once; a login code is kept nowhere).
export const UNKEPT = new Set(['device.pair', 'device.redeem', 'account.login_code']);

export interface Conn {
  transport: Transport;
  role: Role;
  caller: Caller;
  helloed: boolean;
  topics: Set<Topic> | null;
  attached: Map<string, Attach>; // terminal id -> how this connection watches it
  network: boolean; // came in through the paired-device listener
  paired: boolean; // a local connection always; a network one once hello carried a known token
}

export interface FakeDaemonOptions {
  retention?: number; // events kept for resume; older ones give `gap`
  now?: () => Date;
  accounts?: boolean; // serve the account methods (default); false is a daemon that does not yet
  transcript?: boolean; // serve the transcript stream (default); false is a daemon that does not yet
  admin?: boolean; // serve stats, outlook, settings, standing answers, repos and worktrees (default); false is a daemon that does not yet
  chat?: boolean; // serve the chat methods (default); false is a daemon that does not yet
  inbox?: boolean; // serve the inbox methods (default); false is a daemon that does not yet
  bytes?: boolean; // offer the bytes terminal format (default); false is a daemon that serves text only
}

export type Body = { result?: unknown; error?: { code: number; message: string; data: Record<string, unknown> } };

/** The error body a failure answers with. */
export const failBody = (e: Failure): Body => ({ error: { code: e.rpc ?? RPC_CODES[e.code] ?? -32603, message: e.message, data: e.data } });
