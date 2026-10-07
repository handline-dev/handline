import { refused, type Failure } from './errors.ts';

// Calls that belong to the connection, not to a view: never delayed or refused, so a test can still connect.
const HANDSHAKE = new Set(['hello', 'subscribe', 'unsubscribe', 'whoami', 'device.redeem']);

/** Test controls: how long the daemon takes to answer, and calls it refuses. */
export class Knobs {
  delayMs = 0;
  private refusals = new Map<string, { gate: string; reason: string; hint?: string; times: number }>();

  /** Every call after hello waits this long before it is handled; 0 turns it off. */
  latency(ms: number): void {
    this.delayMs = ms;
  }
  /** Account names accounts.list serves; null, as the Python daemon today, is refused as unsupported. */
  accounts: string[] | null = null;
  /** Refuses `method` with the gate's own words, `times` calls (default: until `clear`). */
  refuse(method: string, gate: string, reason: string, hint?: string, times = Infinity): void {
    this.refusals.set(method, { gate, reason, hint, times });
  }
  clear(): void {
    this.refusals.clear();
    this.delayMs = 0;
  }
  waits(method: string): number {
    return HANDSHAKE.has(method) ? 0 : this.delayMs;
  }
  /** The refusal to answer with, if one is set for this call. */
  check(method: string): Failure | undefined {
    const r = this.refusals.get(method);
    if (!r) return undefined;
    if (--r.times <= 0) this.refusals.delete(method);
    return refused(r.gate, r.reason, r.hint);
  }
}
