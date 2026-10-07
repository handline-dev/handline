// The shell's side of window.handline (docs/electron-bridge.md §2, §3): what the page last said, and the check each call passes.
// Electron's own objects are injected, so this runs under node.
import type { Fail } from '@handline/app-core';

export const CHANNELS = {
  screen: 'handline:screen',
  unsent: 'handline:unsent',
  navigate: 'handline:navigate',
  openPath: 'handline:openPath',
  compose: 'handline:compose',
  cancelCompose: 'handline:cancelCompose',
  token: 'handline:token',
} as const;

/** The part of an IPC event this checks: the frame that sent it. */
export interface Sender {
  senderFrame: { url: string; parent: unknown } | null;
}

export type Reply = { ok: true } | Fail<'bad_request'>;
const bad = (message: string): Reply => ({ ok: false, code: 'bad_request', message });

/** True only for the window's top frame, on the origin the shell serves. */
export function fromWindow(e: Sender, origin: string): boolean {
  const f = e.senderFrame;
  if (!f || f.parent !== null) return false;
  try {
    return new URL(f.url).origin === origin;
  } catch {
    return false;
  }
}

const MAX_NAME = 512;

/** What the page last told the shell. `unsent` is null until the page has called it once: until then the shell probes the page itself. */
export class Page {
  agent: string | null = null;
  unsent: boolean | null = null;
  private origin: string;
  constructor(origin: string) {
    this.origin = origin;
  }

  screen(e: Sender, arg: unknown): Reply {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    if (!isObject(arg, ['agent'])) return bad('screen takes { agent }');
    const agent = arg.agent;
    if (agent !== null && (typeof agent !== 'string' || agent.length > MAX_NAME)) return bad('agent is a name or null');
    this.agent = agent;
    return { ok: true };
  }

  setUnsent(e: Sender, arg: unknown): Reply {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    if (typeof arg !== 'boolean') return bad('unsent takes true or false');
    this.unsent = arg;
    return { ok: true };
  }
}

/** A plain object with exactly these keys. */
function isObject(v: unknown, keys: string[]): v is Record<string, unknown> {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return false;
  const have = Object.keys(v);
  return have.length === keys.length && keys.every((k) => have.includes(k));
}
