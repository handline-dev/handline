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
  private unserved = new Set<string>();
  /** Leaves `method` out of hello's list and refuses it, as a daemon that does not serve it would. */
  unserve(method: string): void {
    this.unserved.add(method);
  }
  lists(method: string): boolean {
    return !this.unserved.has(method);
  }
  private holds = new Map<string, Promise<void>>();
  /** Holds every call of `method` until the returned function is called: a test can look at the pending state as long as it likes. */
  hold(method: string): () => void {
    let release!: () => void;
    this.holds.set(
      method,
      new Promise<void>((r) => {
        release = () => {
          this.holds.delete(method);
          r();
        };
      }),
    );
    return release;
  }
  /** The promise a held call waits on, if `method` is held. */
  held(method: string): Promise<void> | undefined {
    return this.holds.get(method);
  }
  clear(): void {
    this.holds.clear();
    this.refusals.clear();
    this.unserved.clear();
    this.delayMs = 0;
  }
  waits(method: string): number {
    return HANDSHAKE.has(method) ? 0 : this.delayMs;
  }
  /** Runs `run` at once, or after the latency, or once the call's hold is released. */
  defer(method: string, run: () => void): void {
    const held = this.held(method);
    const wait = this.waits(method);
    if (held) void held.then(run);
    else if (wait > 0) setTimeout(run, wait);
    else run();
  }
  /** The refusal to answer with, if one is set for this call. */
  check(method: string): Failure | undefined {
    const r = this.refusals.get(method);
    if (!r) return undefined;
    if (--r.times <= 0) this.refusals.delete(method);
    return refused(r.gate, r.reason, r.hint);
  }
}
