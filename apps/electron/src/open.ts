// openPath (docs/electron-bridge.md §4.1): a path from a message opens in the default app, or is revealed when it is something that runs.
// Electron's openers and the worktree lookup are injected, so this runs under node. Nothing here runs a shell.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import type { Fail } from '@handline/app-core';
import { fromWindow, type Sender } from './bridge.ts';

export type OpenReply = { ok: true; how: 'opened' | 'revealed' } | Fail<'bad_request' | 'not_found' | 'unavailable'>;
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
    if (Object.keys(a).some((k) => k !== 'path' && k !== 'agent')) return fail('bad_request', 'openPath takes { path, agent? }');
    if (typeof a.path !== 'string' || (a.agent !== undefined && (typeof a.agent !== 'string' || a.agent.length > 512)))
      return fail('bad_request', 'path and agent are text');
    const raw = a.path;
    if (!raw || raw.length >= 4096 || CONTROL.test(raw)) return fail('bad_request', 'that is not a path');
    if (/^[A-Za-z]{2,}:/.test(raw)) return fail('bad_request', 'only a path opens, not a link');
    if (/^(\\\\|\/\/)/.test(raw)) return fail('bad_request', 'a network path is not opened');
    const abs = await this.resolve(raw, a.agent as string | undefined);
    if (!abs) return fail('not_found', `${raw} is not in this agent's worktree`);
    let real: string;
    let st;
    try {
      real = await fs.realpath(abs);
      st = await fs.stat(real);
    } catch {
      return fail('not_found', `${raw} does not exist`);
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
