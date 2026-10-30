// The shell's side of window.handline (docs/electron-bridge.md §2, §3): what the page last said, and the check each call passes.
// Electron's own objects are injected, so this runs under node.
import { PAGE_SETTINGS, type Fail } from '@handline/app-core';
import type { Methods } from '@handline/client';
import type { CliState } from '#src/cli.ts';
import { UPDATE_ACTS, type UpdateAct, type UpdateView } from '#src/update-flow.ts';
import { parseMode, type Mode, type OfferInput } from '#src/mode.ts';

export const CHANNELS = {
  screen: 'handline:screen',
  unsent: 'handline:unsent',
  navigate: 'handline:navigate',
  fullScreen: 'handline:full-screen',
  openPath: 'handline:openPath',
  bringIn: 'handline:bringIn',
  compose: 'handline:compose',
  cancelCompose: 'handline:cancelCompose',
  mode: 'handline:mode',
  setMode: 'handline:setMode',
  follow: 'handline:follow',
  checkUpdate: 'handline:checkUpdate',
  build: 'handline:build',
  restartDaemon: 'handline:restartDaemon',
  cli: 'handline:cli',
  linkCli: 'handline:linkCli',
  pairRemote: 'handline:pairRemote',
  phone: 'handline:phone',
  update: 'handline:update',
  updateAct: 'handline:updateAct',
  token: 'handline:token',
  captureWindows: 'handline:captureWindows',
  captureWindow: 'handline:captureWindow',
  settingsGet: 'handline:settingsGet',
  settingsSet: 'handline:settingsSet',
  preview: 'handline:preview',
  capturePreview: 'handline:capturePreview',
  deviceShot: 'handline:deviceShot',
  deviceTap: 'handline:deviceTap',
  deviceList: 'handline:deviceList',
  deviceSwipe: 'handline:deviceSwipe',
  deviceText: 'handline:deviceText',
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
  /** The follow-main state, or null in a build that does not follow main. */
  follow(): { sha: string; state: 'current' | 'available' | 'updating' | 'ready' | 'failed'; next: string | null } | null;
  /** The build the app is, and whether the daemon here is one it can move onto that build. */
  build(): { sha: string | null; canRestart: boolean };
  /** Moves the daemon onto the app's build; rejects with the reason. */
  restartDaemon(): Promise<void>;
  /** Looks at GitHub's main now; settles when the look is done. */
  check(): Promise<void>;
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
  follow(e: Sender) {
    if (!fromWindow(e, this.origin)) return bad('not from the app window') as Fail<'bad_request'>;
    const f = this.d.follow();
    return { ok: true as const, following: !!f, sha: f?.sha ?? '', state: f?.state ?? 'current', next: f?.next ?? null };
  }
  build(e: Sender) {
    return fromWindow(e, this.origin) ? { ok: true as const, ...this.d.build() } : (bad('not from the app window') as Fail<'bad_request'>);
  }
  async restartDaemon(e: Sender): Promise<Reply | Fail<'cannot_restart'>> {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    try {
      await this.d.restartDaemon();
      return { ok: true };
    } catch (err) {
      return { ok: false, code: 'cannot_restart', message: err instanceof Error ? err.message : String(err) };
    }
  }
  async checkUpdate(e: Sender): Promise<Reply> {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    await this.d.check();
    return { ok: true };
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

export interface CliDeps {
  state(): CliState;
  /** Links the bundled hl; throws a sentence when it cannot. */
  link(): void;
}

/** The page's way to see and put right the `hl` command on the person's PATH, from the window only. */
export class Cli {
  private origin: string;
  private d: CliDeps;
  constructor(origin: string, d: CliDeps) {
    this.origin = origin;
    this.d = d;
  }
  state(e: Sender): ({ ok: true } & CliState) | Fail<'bad_request'> {
    return fromWindow(e, this.origin) ? { ok: true, ...this.d.state() } : (bad('not from the app window') as Fail<'bad_request'>);
  }
  link(e: Sender): Reply | Fail<'cannot_link'> {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    try {
      this.d.link();
      return { ok: true };
    } catch (err) {
      return { ok: false, code: 'cannot_link', message: err instanceof Error ? err.message : String(err) };
    }
  }
}

export interface SettingsDeps {
  /** settings.get on the shell's own (founder) daemon connection. */
  get(agent: string | undefined): Promise<Methods['settings.get']['result']>;
  /** settings.set on the same connection. */
  set(changes: Record<string, unknown>, agent: string | undefined): Promise<Methods['settings.set']['result']>;
}

export type SettingsFail = Fail<'bad_request' | 'refused' | 'unavailable'>;
const SETTING_KEYS: readonly string[] = PAGE_SETTINGS;
const MAX_CHANGES = 64;
const MAX_VALUE = 4096;
const refuse = (message: string): SettingsFail => ({ ok: false, code: 'bad_request', message });

/** settings.get and settings.set for the page, which connects as a paired device: only these two, with the founder's connection the shell holds. */
export class Settings {
  private origin: string;
  private d: SettingsDeps;
  constructor(origin: string, d: SettingsDeps) {
    this.origin = origin;
    this.d = d;
  }

  async get(e: Sender, arg: unknown): Promise<{ ok: true; result: Methods['settings.get']['result'] } | SettingsFail> {
    if (!fromWindow(e, this.origin)) return refuse('not from the app window');
    const a = arg ?? {};
    if (!isPlain(a) || Object.keys(a).some((k) => k !== 'agent') || !optionalName(a.agent)) return refuse('settingsGet takes { agent? }');
    return this.run(() => this.d.get(a.agent as string | undefined));
  }

  async set(e: Sender, arg: unknown): Promise<{ ok: true; result: Methods['settings.set']['result'] } | SettingsFail> {
    if (!fromWindow(e, this.origin)) return refuse('not from the app window');
    if (!isPlain(arg) || Object.keys(arg).some((k) => k !== 'changes' && k !== 'agent') || !optionalName(arg.agent))
      return refuse('settingsSet takes { changes, agent? }');
    const changes = arg.changes;
    const entries = isPlain(changes) ? Object.entries(changes) : null;
    const okChanges =
      entries !== null &&
      entries.length > 0 &&
      entries.length <= MAX_CHANGES &&
      entries.every(
        ([k, v]) =>
          SETTING_KEYS.includes(k) &&
          (v === null ||
            typeof v === 'boolean' ||
            (typeof v === 'number' && Number.isFinite(v)) ||
            (typeof v === 'string' && v.length <= MAX_VALUE)),
      );
    if (!okChanges) return refuse('changes are settings the page edits, with a number, true or false, text or null');
    return this.run(() => this.d.set(changes as Record<string, unknown>, arg.agent as string | undefined));
  }

  private async run<R>(f: () => Promise<R>): Promise<{ ok: true; result: R } | SettingsFail> {
    try {
      return { ok: true, result: await f() };
    } catch (err) {
      const code = (err as { code?: unknown }).code;
      return {
        ok: false,
        code: code === 'refused' || code === 'bad_request' ? code : 'unavailable',
        message: err instanceof Error ? err.message : String(err),
      };
    }
  }
}

const optionalName = (v: unknown): boolean => v === undefined || (typeof v === 'string' && v.length > 0 && v.length <= MAX_NAME);
const isPlain = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

/** A plain object with exactly these keys. */
function isObject(v: unknown, keys: string[]): v is Record<string, unknown> {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return false;
  const have = Object.keys(v);
  return have.length === keys.length && keys.every((k) => have.includes(k));
}

export interface UpdateDeps {
  view(): UpdateView;
  /** A button of the update notice: restart, later or try again. */
  act(what: Exclude<UpdateAct, 'details'>): void;
  /** Opens the log behind a failed update. */
  details(): void;
}

/** The update notice's side in the shell: the state it draws, and its buttons, with the usual sender and argument checks. */
export class Update {
  private origin: string;
  private d: UpdateDeps;
  constructor(origin: string, d: UpdateDeps) {
    this.origin = origin;
    this.d = d;
  }
  view(e: Sender): ({ ok: true } & UpdateView) | Fail<'bad_request'> {
    return fromWindow(e, this.origin) ? { ok: true, ...this.d.view() } : (bad('not from the app window') as Fail<'bad_request'>);
  }
  act(e: Sender, arg: unknown): Reply {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    if (typeof arg !== 'string' || !UPDATE_ACTS.includes(arg as UpdateAct)) return bad('updateAct takes restart, later, retry or details');
    if (arg === 'details') this.d.details();
    else this.d.act(arg as Exclude<UpdateAct, 'details'>);
    return { ok: true };
  }
}
