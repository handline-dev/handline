import type { Ctx } from './cache.ts';
import type { Key } from './reads.ts';

/**
 * One row of the writes table (docs/turborepo-layout.md §8.5).
 * - authored: the person's own data. The cache is edited at once and put back if the daemon refuses.
 * - decided: state only the daemon decides. The cache is not touched; the view shows a pending mark.
 * - uncached: a stream write (terminal input); the stream is the answer.
 */
export interface WriteSpec {
  kind: 'authored' | 'decided' | 'uncached';
  /** What the error line calls the action: "send", "answer". */
  label: string;
  /** A mark the app makes on its own: its success leaves the error line alone, so it cannot wipe a refusal before it is read. */
  quiet?: boolean;
  /** The entries `patch` edits: snapshotted first, and read again after if no topic keeps them current. */
  touches?: (d: string, p: never) => Key[];
  /** The cache as the write leaves it, assuming the daemon accepts. */
  patch?: (c: Ctx, p: never) => void;
  /** After the daemon accepted: swap what the cache guessed for what the daemon said. */
  reconcile?: (c: Ctx, p: never, result: never) => void;
  /** After a refusal: true if this write handled its own failure (and the snapshot is not put back). */
  fail?: (c: Ctx, p: never, e: unknown) => boolean;
}
