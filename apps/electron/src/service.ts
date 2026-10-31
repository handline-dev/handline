// The installed app does not start the daemon itself: its own carried `handlined service ensure` puts the daemon on this app's version and keeps it
// running under launchd (the Go service package does the work). The app runs it at every launch and shows its one JSON line.
import { execFile } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';

export type EnsureState = 'running' | 'started' | 'upgraded' | 'kept' | 'failed';

export interface Ensured {
  state: EnsureState;
  version: string;
  /** Why the daemon is in this state; empty when there was nothing to say. */
  reason: string;
}

/** Where the service keeps the versions it copied out of the bundle; `current` links to the one the job runs. */
export const serviceBin = (home: string = os.homedir()): string => path.join(home, 'Library', 'Application Support', 'Handline', 'bin');

/** The `hl` the Settings link points at: through `current`, so it survives the bundle being moved, replaced or trashed. */
export const currentHl = (home?: string): string => path.join(serviceBin(home), 'current', 'hl');

const STATES: readonly string[] = ['running', 'started', 'upgraded', 'kept', 'failed'];

/** The line `service ensure` printed, or a failure that says what came out instead. */
export function parseEnsure(out: string): Ensured {
  const line = out.trim().split('\n').pop() ?? '';
  try {
    const j = JSON.parse(line) as Partial<Ensured>;
    if (typeof j.state === 'string' && STATES.includes(j.state)) {
      return { state: j.state as EnsureState, version: j.version ?? '', reason: j.reason ?? '' };
    }
  } catch {
    /* not a line of ours */
  }
  return { state: 'failed', version: '', reason: line ? `service ensure said: ${line}` : 'service ensure printed nothing' };
}

export type Run = (exe: string, args: string[]) => Promise<{ out: string; error?: string }>;

/** Runs a command to its end; a non-zero exit still hands back what it printed (ensure prints its line with exit 1 for failed and kept). */
export const run: Run = (exe, args) =>
  new Promise((resolve) => {
    execFile(exe, args, { timeout: 120_000 }, (e, stdout, stderr) => {
      resolve({ out: stdout, error: e && !stdout.trim() ? stderr.trim() || e.message : undefined });
    });
  });

/** `handlined service ensure` from the app's own bundle; with `gateway` the service also runs the gateway on that address (and name) and waits for it. */
export async function ensureService(
  handlined: string,
  gateway?: { listen: string; advertise: string | null },
  exec: Run = run,
): Promise<Ensured> {
  const flags = gateway ? ['--listen', gateway.listen, ...(gateway.advertise ? ['--advertise', gateway.advertise] : [])] : [];
  const r = await exec(handlined, ['service', 'ensure', ...flags]);
  return r.error ? { state: 'failed', version: '', reason: r.error } : parseEnsure(r.out);
}

/** `handlined service stop`: the job is unloaded and its file kept, so the next launch starts it again. Throws with the reason. */
export async function stopService(handlined: string, exec: Run = run): Promise<void> {
  const r = await exec(handlined, ['service', 'stop']);
  if (r.error) throw new Error(r.error);
}
