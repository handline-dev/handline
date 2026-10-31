// openPath (docs/electron-bridge.md §4.1): a path from a message opens in the default app, or is revealed when it is something that runs.
// Electron's openers and the worktree lookup are injected, so this runs under node. Nothing here runs a shell.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import type { Fail } from '@handline/app-core';
import { fromWindow, type Sender } from '#src/bridge.ts';

export type OpenReply = { ok: true; how: 'opened' | 'revealed' | 'branch' | 'main' | 'github' } | Fail<'bad_request' | 'not_found' | 'unavailable'>;
const fail = (code: 'bad_request' | 'not_found' | 'unavailable', message: string): OpenReply => ({ ok: false, code, message });

/** Extensions of things the OS would run, install or script when opened: these are revealed, never opened. A minimum, not a complete list. */
const RUNS = new Set(
  (
    'app command tool terminal workflow action scpt scptd applescript sh bash zsh fish csh ksh pl rb py pyw js mjs jse jar jnlp exe bat cmd com scr ps1 vbs vbe wsf wsh hta cpl msc run appimage desktop ' +
    'pkg mpkg dmg iso msi msp msix appx appinstaller deb rpm flatpakref snap reg pif mobileconfig settingcontent-ms appref-ms ' +
    'lnk url webloc inetloc fileloc scf shortcut ' +
    'html htm xhtml svg ' +
    'prefpane saver qlgenerator plugin bundle'
  ).split(' '),
);

export interface OpenDeps {
  home?: string;
  /** The agent's worktree path as the daemon says it, never as the page says it; null when it has none. */
  worktreeOf(agent: string): Promise<string | null>;
  open(p: string): Promise<string>; // Electron's shell.openPath: '' or an error string
  reveal(p: string): void; // shell.showItemInFolder
  /** The git side, all read-only and run in the agent's worktree; each is absent in a shell without git. */
  git?: {
    /** Whether the branch exists in the worktree's repo (local or on origin). */
    hasBranch(cwd: string, name: string): Promise<boolean>;
    /** `git show <ref>:<file>`: the bytes, or null when the ref or the file is not there. */
    show(cwd: string, ref: string, file: string): Promise<Buffer | null>;
    /** The GitHub `owner/repo` of origin, or null. */
    repoOf(cwd: string): Promise<string | null>;
  };
  openExternal?(url: string): Promise<void>;
  /** Where a git version is written to be opened: a fresh folder, the file read-only. */
  scratch?(name: string, bytes: Buffer): Promise<string>;
}

// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/;

export class Opener {
  private origin: string;
  private deps: OpenDeps;
  constructor(origin: string, deps: OpenDeps) {
    this.origin = origin;
    this.deps = deps;
  }

  async openPath(e: Sender, arg: unknown): Promise<OpenReply> {
    if (!fromWindow(e, this.origin)) return fail('bad_request', 'not from the app window');
    if (typeof arg !== 'object' || arg === null || Array.isArray(arg)) return fail('bad_request', 'openPath takes { path, agent? }');
    const a = arg as Record<string, unknown>;
    if (Object.keys(a).some((k) => k !== 'path' && k !== 'agent' && k !== 'branches')) return fail('bad_request', 'openPath takes { path, agent? }');
    if (typeof a.path !== 'string' || (a.agent !== undefined && (typeof a.agent !== 'string' || a.agent.length > 512)))
      return fail('bad_request', 'path and agent are text');
    if (
      a.branches !== undefined &&
      (!Array.isArray(a.branches) || a.branches.length > 20 || a.branches.some((b) => typeof b !== 'string' || b.length > 200))
    )
      return fail('bad_request', 'branches are up to 20 names');
    const branches = (a.branches ?? []) as string[];
    const raw = a.path;
    if (!raw || raw.length >= 4096 || CONTROL.test(raw)) return fail('bad_request', 'that is not a path');
    if (/^[A-Za-z]{2,}:/.test(raw)) return fail('bad_request', 'only a path opens, not a link');
    if (/^(\\\\|\/\/)/.test(raw)) return fail('bad_request', 'a network path is not opened');
    const agent = a.agent as string | undefined;
    const rel = /^(~|\/)/.test(raw) ? null : path.posix.normalize(raw.replace(/\\/g, '/'));
    const relOk = rel !== null && rel !== '.' && !rel.startsWith('../') && rel !== '..';
    const wt = relOk && agent ? await this.deps.worktreeOf(agent).catch(() => null) : null;
    // A repo path: the version on a branch the message names, then the worktree's own file, then main, then GitHub.
    if (relOk && wt && this.deps.git) {
      const viaBranch = await this.fromGit(wt, rel, branches);
      if (viaBranch) return viaBranch;
    }
    const abs = await this.resolve(raw, agent);
    let real: string | null = null;
    let st;
    if (abs)
      try {
        real = await fs.realpath(abs);
        st = await fs.stat(real);
      } catch {
        real = null;
      }
    if (!real || !st) {
      if (relOk && wt && this.deps.git) {
        const later = await this.fromGit(wt, rel, branches, true);
        if (later) return later;
      }
      return fail('not_found', abs ? `${raw} does not exist` : `${raw} is not in this agent's worktree`);
    }
    const ext = path.extname(real).slice(1).toLowerCase();
    const runs = st.isDirectory() ? ext !== '' : !st.isFile() || (st.mode & 0o111) !== 0 || RUNS.has(ext);
    if (runs) {
      this.deps.reveal(real);
      return { ok: true, how: 'revealed' };
    }
    const err = await this.deps.open(real);
    return err ? fail('unavailable', err) : { ok: true, how: 'opened' };
  }

  /** The git versions of a repo path, read-only: the named branches, or (`last`) main, then the same on GitHub. Null when none has it. */
  private async fromGit(wt: string, rel: string, branches: string[], last = false): Promise<OpenReply | null> {
    const git = this.deps.git!;
    const refs: { ref: string; how: 'branch' | 'main' }[] = [];
    if (last) refs.push({ ref: 'main', how: 'main' });
    else
      for (const b of branches)
        if (/^[\w][\w./-]*$/.test(b) && (await git.hasBranch(wt, b).catch(() => false))) refs.push({ ref: b, how: 'branch' });
    for (const { ref, how } of refs) {
      const bytes = await git.show(wt, ref, rel).catch(() => null);
      if (!bytes || !this.deps.scratch) continue;
      const file = await this.deps.scratch(path.posix.basename(rel), bytes);
      const ext = path.extname(file).slice(1).toLowerCase();
      if (RUNS.has(ext)) {
        this.deps.reveal(file);
        return { ok: true, how: 'revealed' };
      }
      const err = await this.deps.open(file);
      return err ? fail('unavailable', err) : { ok: true, how };
    }
    if (!last || !this.deps.openExternal) return null;
    const repo = await git.repoOf(wt).catch(() => null);
    if (!repo) return null;
    const where = branches.find((b) => /^[\w][\w./-]*$/.test(b)) ?? 'main';
    await this.deps.openExternal(`https://github.com/${repo}/blob/${where}/${rel.split('/').map(encodeURIComponent).join('/')}`);
    return { ok: true, how: 'github' };
  }

  /** `~/x` is the home; `/x` as is; anything else is relative to the agent's worktree and must stay inside it. */
  private async resolve(raw: string, agent: string | undefined): Promise<string | null> {
    if (raw === '~' || raw.startsWith('~/')) return path.join(this.deps.home ?? os.homedir(), raw.slice(1));
    if (path.isAbsolute(raw)) return raw;
    if (!agent) return null;
    const wt = await this.deps.worktreeOf(agent).catch(() => null);
    if (!wt) return null;
    const joined = path.resolve(wt, raw);
    return joined === wt || joined.startsWith(wt + path.sep) ? joined : null;
  }
}
