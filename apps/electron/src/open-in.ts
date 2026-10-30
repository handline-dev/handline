// openIn and openInApps (docs/electron-bridge.md §4.6): the agent's folder in VS Code or Terminal, or its Claude Code session in Terminal.
// The page names an app by key and an agent by name; the folder and the tmux command come from the daemon on the shell's own connection.
// Only /usr/bin/open runs, with an argument array and no shell, so nothing the page or an agent writes is read as a command.
import fs from 'node:fs/promises';
import path from 'node:path';
import type { Fail } from '@handline/app-core';
import { fromWindow, type Sender } from '#src/bridge.ts';

export type OpenInApp = 'vscode' | 'terminal' | 'claude';
export type OpenInReply = { ok: true } | Fail<'bad_request' | 'not_found' | 'unavailable'>;
const fail = (code: 'bad_request' | 'not_found' | 'unavailable', message: string): OpenInReply => ({ ok: false, code, message });

const OPEN = '/usr/bin/open';
/** Where each app lives, checked in order; `claude` rides on Terminal. */
const APPS: Record<'vscode' | 'terminal', { name: string; at: string[] }> = {
  vscode: { name: 'Visual Studio Code', at: ['/Applications/Visual Studio Code.app', '~/Applications/Visual Studio Code.app'] },
  terminal: { name: 'Terminal', at: ['/System/Applications/Utilities/Terminal.app', '/Applications/Utilities/Terminal.app'] },
};
const KEYS = new Set<string>(['vscode', 'terminal', 'claude']);
const NAME = /^[A-Za-z0-9._-]+$/;
const LIFETIME_MS = 60_000;

export interface OpenInDeps {
  platform: string;
  home: string;
  tmp: string;
  /** The agent's worktree path as the daemon says it; null when it has none. */
  worktreeOf(agent: string): Promise<string | null>;
  /** `attach.argv` of terminal.open: the command that attaches to the agent's tmux session; null when there is none. */
  attachOf(agent: string): Promise<string[] | null>;
  /** Runs a fixed binary with an argument array; rejects when it fails. */
  exec(file: string, args: string[]): Promise<void>;
  /** Whether an app is at this path. */
  exists(p: string): Promise<boolean>;
  /** Calls `fn` after `ms`; the delete of the launcher's folder. */
  later(fn: () => void, ms: number): void;
}

/** The tmux attach command the daemon serves: `tmux -L <server name> attach-session -t <session>`; anything else is not run. */
export function isAttach(argv: string[]): boolean {
  return (
    argv.length === 6 &&
    argv[0] === 'tmux' &&
    argv[1] === '-L' &&
    NAME.test(argv[2]!) &&
    argv[3] === 'attach-session' &&
    argv[4] === '-t' &&
    NAME.test(argv[5]!)
  );
}

/** One word for sh: in single quotes, a quote inside it closed, escaped and reopened. */
export const quote = (word: string): string => `'${word.replace(/'/g, `'\\''`)}'`;

export class OpenIn {
  private origin: string;
  private deps: OpenInDeps;
  constructor(origin: string, deps: OpenInDeps) {
    this.origin = origin;
    this.deps = deps;
  }

  private async has(app: 'vscode' | 'terminal'): Promise<boolean> {
    for (const p of APPS[app].at) if (await this.deps.exists(p.startsWith('~/') ? path.join(this.deps.home, p.slice(2)) : p)) return true;
    return false;
  }

  /** Which of the three this machine has; none off macOS. */
  async apps(e: Sender): Promise<{ ok: true; apps: OpenInApp[] } | Fail<'bad_request'>> {
    if (!fromWindow(e, this.origin)) return { ok: false, code: 'bad_request', message: 'not from the app window' };
    if (this.deps.platform !== 'darwin') return { ok: true, apps: [] };
    const terminal = await this.has('terminal');
    const found: OpenInApp[] = [];
    if (await this.has('vscode')) found.push('vscode');
    if (terminal) found.push('terminal', 'claude');
    return { ok: true, apps: found };
  }

  async open(e: Sender, arg: unknown): Promise<OpenInReply> {
    if (!fromWindow(e, this.origin)) return fail('bad_request', 'not from the app window');
    if (typeof arg !== 'object' || arg === null || Array.isArray(arg)) return fail('bad_request', 'openIn takes { app, agent }');
    const a = arg as Record<string, unknown>;
    if (Object.keys(a).some((k) => k !== 'app' && k !== 'agent')) return fail('bad_request', 'openIn takes { app, agent }');
    if (typeof a.app !== 'string' || !KEYS.has(a.app)) return fail('bad_request', 'app is vscode, terminal or claude');
    if (typeof a.agent !== 'string' || !a.agent || a.agent.length > 512 || !NAME.test(a.agent))
      return fail('bad_request', 'agent is a name');
    if (this.deps.platform !== 'darwin') return fail('unavailable', 'Open in works on a Mac');
    const app = a.app as OpenInApp;
    const agent = a.agent;
    const wt = await this.deps.worktreeOf(agent).catch(() => null);
    if (!wt) return fail('not_found', `${agent} has no folder`);
    let dir: string;
    try {
      dir = await fs.realpath(wt);
      if (!(await fs.stat(dir)).isDirectory()) return fail('not_found', `${agent}'s folder is not a folder`);
    } catch {
      return fail('not_found', `${agent}'s folder is not on this machine`);
    }
    if (app === 'claude') return this.claude(agent);
    if (!(await this.has(app))) return fail('unavailable', `${APPS[app].name} is not installed`);
    return this.run(['-a', APPS[app].name, '--', dir]);
  }

  private async run(args: string[]): Promise<OpenInReply> {
    try {
      await this.deps.exec(OPEN, args);
      return { ok: true };
    } catch (err) {
      return fail('unavailable', err instanceof Error ? err.message : String(err));
    }
  }

  /** A launcher file in a folder of its own that runs the attach command, opened by Terminal; the folder goes after a minute. */
  private async claude(agent: string): Promise<OpenInReply> {
    if (!(await this.has('terminal'))) return fail('unavailable', 'Terminal is not installed');
    const argv = await this.deps.attachOf(agent).catch(() => null);
    if (!argv || !isAttach(argv)) return fail('unavailable', `${agent} has no terminal to open`);
    const dir = await fs.mkdtemp(path.join(this.deps.tmp, 'handline-'));
    const file = path.join(dir, 'handline-claude.command');
    const drop = () => void fs.rm(dir, { recursive: true, force: true }).catch(() => undefined);
    try {
      await fs.writeFile(file, `#!/bin/sh\nexec ${argv.map(quote).join(' ')}\n`, { flag: 'wx', mode: 0o700 });
      await fs.chmod(file, 0o700);
    } catch (err) {
      drop();
      return fail('unavailable', err instanceof Error ? err.message : String(err));
    }
    this.deps.later(drop, LIFETIME_MS);
    const r = await this.run(['-a', APPS.terminal.name, file]);
    if (!r.ok) drop();
    return r;
  }
}
