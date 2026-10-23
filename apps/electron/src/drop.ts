// bringIn (docs/electron-bridge.md §4.5): a file or folder dropped from Finder is copied into the agent's worktree, so the agent reads
// it without a permission of its own for Desktop, Downloads and the like. The folder keeps itself out of git.
import fs from 'node:fs/promises';
import path from 'node:path';
import type { Fail } from '@handline/app-core';
import { fromWindow, type Sender } from '#src/bridge.ts';

export type BringReply = { ok: true; path: string } | Fail<'bad_request' | 'not_found' | 'unavailable'>;
const fail = (code: 'bad_request' | 'not_found' | 'unavailable', message: string): BringReply => ({ ok: false, code, message });

const DIR = '.handline-drops';

export class Dropper {
  private origin: string;
  /** The agent's worktree path as the daemon says it, never as the page says it; null when it has none. */
  private worktreeOf: (agent: string) => Promise<string | null>;
  constructor(origin: string, worktreeOf: (agent: string) => Promise<string | null>) {
    this.origin = origin;
    this.worktreeOf = worktreeOf;
  }

  async bringIn(e: Sender, arg: unknown): Promise<BringReply> {
    if (!fromWindow(e, this.origin)) return fail('bad_request', 'not from the app window');
    const a = arg as Record<string, unknown> | null;
    if (typeof a !== 'object' || a === null || Object.keys(a).some((k) => k !== 'path' && k !== 'agent'))
      return fail('bad_request', 'bringIn takes { path, agent }');
    if (
      typeof a.path !== 'string' ||
      typeof a.agent !== 'string' ||
      !path.isAbsolute(a.path) ||
      a.path.length >= 4096 ||
      a.agent.length > 512
    )
      return fail('bad_request', 'path is absolute and agent is a name');
    const wt = await this.worktreeOf(a.agent).catch(() => null);
    if (!wt) return fail('unavailable', 'this agent has no worktree to copy into');
    const name = path.basename(a.path);
    try {
      await fs.stat(a.path);
    } catch {
      return fail('not_found', `${name} does not exist`);
    }
    try {
      const base = path.join(wt, DIR);
      await fs.mkdir(base, { recursive: true });
      await fs.writeFile(path.join(base, '.gitignore'), '*\n');
      // One folder to a drop, so the same name twice never overwrites.
      const dir = await fs.mkdtemp(path.join(base, 'd-'));
      const to = path.join(dir, name);
      await fs.cp(a.path, to, { recursive: true, verbatimSymlinks: true });
      return { ok: true, path: to };
    } catch (err) {
      return fail('unavailable', err instanceof Error ? err.message : String(err));
    }
  }
}
