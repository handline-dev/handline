// The shell's side of window.handline (docs/electron-bridge.md §2, §3): what the page last said, and the check each call passes.
// Electron's own objects are injected, so this runs under node.
import type { Fail } from '@handline/app-core';
import { parseMode, type Mode, type OfferInput } from './mode.ts';

export const CHANNELS = {
  screen: 'handline:screen',
  unsent: 'handline:unsent',
  navigate: 'handline:navigate',
  openPath: 'handline:openPath',
  compose: 'handline:compose',
  cancelCompose: 'handline:cancelCompose',
  mode: 'handline:mode',
  setMode: 'handline:setMode',
  pairRemote: 'handline:pairRemote',
  phone: 'handline:phone',
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

export interface ConnectionDeps {
  mode(): { mode: Mode; remote: string | null };
  /** Saves the mode and relaunches. */
  setMode(m: Mode): void;
  /** Pairs and relaunches on the new device; rejects with a sentence when the gateway cannot be paired with. */
  pair(i: OfferInput): Promise<void>;
  /** Opens the phone-pairing QR window; rejects with a sentence when it cannot. */
  phone(): Promise<void>;
}

const OFFER_KEYS = ['link', 'address', 'code'];
const MAX_OFFER = 2048;

/** The page's way to change which daemon the app is on, with the same sender and argument checks as the rest. */
export class Connection {
  private origin: string;
  private d: ConnectionDeps;
  constructor(origin: string, d: ConnectionDeps) {
    this.origin = origin;
    this.d = d;
  }
  mode(e: Sender): ({ ok: true; phone: boolean } & ReturnType<ConnectionDeps['mode']>) | Fail<'bad_request'> {
    return fromWindow(e, this.origin)
      ? { ok: true, ...this.d.mode(), phone: this.d.mode().remote === null }
      : (bad('not from the app window') as Fail<'bad_request'>);
  }
  setMode(e: Sender, arg: unknown): Reply {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    const mode = typeof arg === 'string' ? parseMode(arg) : undefined;
    if (!mode) return bad('setMode takes connect or own');
    this.d.setMode(mode);
    return { ok: true };
  }
  async phone(e: Sender): Promise<Reply | Fail<'cannot_pair'>> {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    try {
      await this.d.phone();
      return { ok: true };
    } catch (err) {
      return { ok: false, code: 'cannot_pair', message: err instanceof Error ? err.message : String(err) };
    }
  }
  async pairRemote(e: Sender, arg: unknown): Promise<Reply | Fail<'cannot_pair'>> {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    const o = arg as Record<string, unknown> | null;
    const okShape =
      typeof o === 'object' &&
      o !== null &&
      !Array.isArray(o) &&
      Object.entries(o).every(([k, v]) => OFFER_KEYS.includes(k) && typeof v === 'string' && v.length <= MAX_OFFER);
    if (!okShape) return bad('pairRemote takes { link } or { address, code }');
    try {
      await this.d.pair(o as OfferInput);
      return { ok: true };
    } catch (err) {
      return { ok: false, code: 'cannot_pair', message: err instanceof Error ? err.message : String(err) };
    }
  }
}

/** A plain object with exactly these keys. */
function isObject(v: unknown, keys: string[]): v is Record<string, unknown> {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return false;
  const have = Object.keys(v);
  return have.length === keys.length && keys.every((k) => have.includes(k));
}
