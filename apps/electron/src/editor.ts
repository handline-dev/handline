// compose (docs/electron-bridge.md §4.2): the text goes to a temp file, the person's editor opens it, the file is read back when the editor exits.
// The editor string is parsed here, never given to a shell; the child leads its own process group so a cancel or a quit kills all of it.
import { spawn as nodeSpawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs/promises';
import { constants } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Fail } from '@handline/app-core';
import { fromWindow, type Sender } from './bridge.ts';

export type ComposeCode =
  | 'bad_request'
  | 'no_editor'
  | 'terminal_editor'
  | 'bad_editor'
  | 'not_waiting'
  | 'editor_failed'
  | 'cancelled'
  | 'busy'
  | 'too_large'
  | 'unavailable';
export type ComposeReply = { ok: true; text: string; changed: boolean } | Fail<ComposeCode>;
const fail = (code: ComposeCode, message: string): ComposeReply => ({ ok: false, code, message });

const MAX_BYTES = 256 * 1024;
const TERMINAL = new Set('vi vim nvim nano pico emacs micro hx helix joe ne'.split(' '));
const WAIT_FLAGS = '`code --wait`, `subl -w` or `zed --wait`';
const FAST_MS = 1500;

/** The editor settings the shell has: from its own environment, else from the login shell once. */
export interface EditorEnv {
  VISUAL?: string;
  EDITOR?: string;
  PATH?: string;
}

/** Splits an editor string into words: spaces separate, quotes and a backslash group; no expansion, and an operator outside quotes is refused. */
export function splitWords(s: string): string[] | null {
  const out: string[] = [];
  let cur = '';
  let has = false;
  let quote: '"' | "'" | null = null;
  for (let i = 0; i < s.length; i++) {
    const c = s[i]!;
    if (quote) {
      if (c === quote) quote = null;
      else if (c === '\\' && quote === '"' && i + 1 < s.length) cur += s[++i];
      else cur += c;
    } else if (c === '"' || c === "'") {
      quote = c;
      has = true;
    } else if (c === '\\' && i + 1 < s.length) {
      cur += s[++i];
      has = true;
    } else if (/[;|&<>$`()\n\r]/.test(c)) return null;
    else if (c === ' ' || c === '\t') {
      if (has || cur) out.push(cur);
      cur = '';
      has = false;
    } else {
      cur += c;
      has = true;
    }
  }
  if (quote) return null;
  if (has || cur) out.push(cur);
  return out;
}

async function executable(p: string): Promise<boolean> {
  try {
    const st = await fs.stat(p);
    if (!st.isFile()) return false;
    await fs.access(p, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/** The first word as a file: taken as is when absolute, else looked up on the given PATH. */
async function lookup(word: string, PATH: string): Promise<string | null> {
  if (path.isAbsolute(word)) return (await executable(word)) ? word : null;
  if (word.includes('/')) return null;
  for (const dir of PATH.split(path.delimiter)) if (dir && (await executable(path.join(dir, word)))) return path.join(dir, word);
  return null;
}

export interface EditorDeps {
  env(): Promise<EditorEnv>;
  tmp?: string;
  spawn?: typeof nodeSpawn;
  kill?(pid: number, signal: NodeJS.Signals): void;
}

export class Composer {
  private origin: string;
  private deps: EditorDeps;
  private child: ChildProcess | null = null;
  private dir: string | null = null;
  private cancelled = false;
  constructor(origin: string, deps: EditorDeps) {
    this.origin = origin;
    this.deps = deps;
  }

  /** True while an editor is open: the shell counts itself unsent, so an update does not restart under it. */
  get running(): boolean {
    return this.dir !== null;
  }

  async cancel(e: Sender, arg: unknown): Promise<{ ok: true } | Fail<'bad_request'>> {
    if (!fromWindow(e, this.origin) || arg !== undefined) return { ok: false, code: 'bad_request', message: 'cancelCompose takes nothing' };
    if (!this.running) return { ok: true }; // nothing to cancel: a late click must not mark the next compose
    this.cancelled = true;
    this.killGroup();
    return { ok: true };
  }

  async compose(e: Sender, arg: unknown): Promise<ComposeReply> {
    if (!fromWindow(e, this.origin)) return fail('bad_request', 'not from the app window');
    const a = arg ?? {};
    if (typeof a !== 'object' || Array.isArray(a)) return fail('bad_request', 'compose takes { text?, ext? }');
    const { text = '', ext = 'md', ...rest } = a as Record<string, unknown>;
    if (Object.keys(rest).length || typeof text !== 'string' || (ext !== 'md' && ext !== 'txt'))
      return fail('bad_request', 'compose takes { text?, ext? }');
    if (Buffer.byteLength(text) > MAX_BYTES) return fail('too_large', 'the text is over 256 KiB');
    if (this.running) return fail('busy', 'an editor is already open');
    this.dir = ''; // claimed before any await, so a second call is busy
    try {
      return await this.run(text, ext);
    } finally {
      const dir = this.dir;
      this.child = null;
      this.dir = null;
      this.cancelled = false;
      if (dir) await fs.rm(dir, { recursive: true, force: true }).catch(() => undefined);
    }
  }

  private async run(text: string, ext: string): Promise<ComposeReply> {
    const env = await this.deps.env();
    const editor = (env.VISUAL ?? '').trim() || (env.EDITOR ?? '').trim();
    if (!editor) return fail('no_editor', 'No editor is set: set VISUAL (for example `code --wait`) and start Handline again.');
    const words = splitWords(editor);
    if (!words?.length) return fail('bad_editor', 'The editor setting has an operator or an open quote; it is not run through a shell.');
    const base = path.basename(words[0]!);
    if (TERMINAL.has(base))
      return fail(
        'terminal_editor',
        `${base} needs a terminal. Set VISUAL to an editor that opens a window and waits, such as ${WAIT_FLAGS}.`,
      );
    const file = await lookup(words[0]!, env.PATH ?? process.env.PATH ?? '');
    if (!file) return fail('bad_editor', `${words[0]} was not found on the PATH or is not executable.`);
    let dir: string;
    let target: string;
    try {
      dir = await fs.mkdtemp(path.join(this.deps.tmp ?? os.tmpdir(), 'handline-'));
      this.dir = dir;
      await fs.chmod(dir, 0o700);
      target = path.join(dir, `compose.${ext}`);
      await fs.writeFile(target, text, { flag: 'wx', mode: 0o600 });
    } catch (err) {
      return fail('unavailable', `The temp file could not be made: ${err instanceof Error ? err.message : String(err)}`);
    }
    if (this.cancelled) return fail('cancelled', 'Cancelled.'); // cancelled while the file was being made: the editor never starts
    const started = Date.now();
    const exit = await new Promise<{ code: number | null; error?: Error }>((resolve) => {
      const child = (this.deps.spawn ?? nodeSpawn)(file, [...words.slice(1), target], {
        shell: false,
        detached: true,
        stdio: 'ignore',
        cwd: dir,
      });
      this.child = child;
      child.once('error', (error) => resolve({ code: null, error }));
      child.once('exit', (code) => resolve({ code }));
    });
    if (this.cancelled) return fail('cancelled', 'Cancelled.');
    if (exit.error) return fail('unavailable', `The editor did not start: ${exit.error.message}`);
    if (exit.code !== 0) return fail('editor_failed', `The editor exited with ${exit.code ?? 'a signal'}; nothing was taken from it.`);
    let back: string;
    try {
      back = await readBack(target);
    } catch (err) {
      return err instanceof TooLarge
        ? fail('too_large', 'The file is over 256 KiB.')
        : fail('unavailable', 'The file could not be read back.');
    }
    if (Date.now() - started < FAST_MS && back === text)
      return fail('not_waiting', `The editor returned at once. Add its wait flag, for example ${WAIT_FLAGS}, to VISUAL.`);
    return { ok: true, text: back, changed: back !== text };
  }

  private killGroup(): void {
    const pid = this.child?.pid;
    if (!pid) return;
    try {
      (this.deps.kill ?? process.kill)(-pid, 'SIGTERM');
    } catch {
      // already gone
    }
  }

  /** On quit: the group is killed and the directory removed before the shell goes. */
  dispose(): void {
    this.killGroup();
    if (this.dir) void fs.rm(this.dir, { recursive: true, force: true });
  }
}

class TooLarge extends Error {}

/** The whole file as UTF-8, opened so that a symlink planted in its place is refused. */
async function readBack(p: string): Promise<string> {
  const h = await fs.open(p, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const st = await h.stat();
    if (!st.isFile()) throw new Error('not a file');
    if (st.size > MAX_BYTES) throw new TooLarge();
    return (await h.readFile('utf8')).toString();
  } finally {
    await h.close();
  }
}

/** At launch: handline-* temp directories this user owns and that are older than a day are leftovers of a crash. */
export async function sweepTemp(tmp = os.tmpdir(), now = Date.now()): Promise<void> {
  for (const name of await fs.readdir(tmp).catch(() => [])) {
    if (!name.startsWith('handline-')) continue;
    const p = path.join(tmp, name);
    const st = await fs.lstat(p).catch(() => null);
    if (st?.isDirectory() && st.uid === process.getuid?.() && now - st.mtimeMs > 24 * 3600 * 1000)
      await fs.rm(p, { recursive: true, force: true }).catch(() => undefined);
  }
}

/** VISUAL, EDITOR and PATH from the login shell, printed between markers (the rc files may print too). Asked once; null on any failure. */
export function parseLoginEnv(out: string): EditorEnv {
  const m = out.match(/__HL_BEGIN__\n([\s\S]*?)\n__HL_END__/);
  const lines = (m?.[1] ?? '').split('\n');
  const get = (k: string) => lines.find((l) => l.startsWith(`${k}=`))?.slice(k.length + 1) || undefined;
  return { VISUAL: get('VISUAL'), EDITOR: get('EDITOR'), PATH: get('PATH') };
}
export const LOGIN_PROBE = 'printf "__HL_BEGIN__\\nVISUAL=%s\\nEDITOR=%s\\nPATH=%s\\n__HL_END__\\n" "$VISUAL" "$EDITOR" "$PATH"';
