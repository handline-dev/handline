// The Android and iOS tabs: a picture of a running Android emulator or device (adb) or iOS simulator (simctl), and taps, drags and typed text on
// the picked one (Android through adb, a simulator through idb). The page names the kind and the device; the commands and their arguments are fixed here.
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Fail } from '@handline/app-core';
import { fromWindow, type Sender } from '#src/bridge.ts';

type Run = (file: string, args: string[]) => Promise<Buffer>;
const run: Run = (file, args) =>
  new Promise((resolve, reject) =>
    execFile(file, args, { encoding: 'buffer', maxBuffer: 64 * 1024 * 1024, timeout: 10_000 }, (err, out) =>
      err ? reject(err) : resolve(out),
    ),
  );

const unavailable = (message: string): Fail<'unavailable'> => ({ ok: false, code: 'unavailable', message });
const bad = (message: string): Fail<'bad_request'> => ({ ok: false, code: 'bad_request', message });

/** adb from the Android SDK when it is installed in the usual places, else the one on the PATH. */
export function adbPath(env: NodeJS.ProcessEnv = process.env, exists: (p: string) => boolean = fs.existsSync): string {
  const roots = [env.ANDROID_HOME, env.ANDROID_SDK_ROOT, path.join(os.homedir(), 'Library/Android/sdk')].filter((r): r is string => !!r);
  return roots.map((r) => path.join(r, 'platform-tools/adb')).find(exists) ?? 'adb';
}

/** The width and height a PNG says in its header; null when it is not one. */
export function pngSize(png: Buffer): { width: number; height: number } | null {
  return png.length > 24 && png.readUInt32BE(12) === 0x49484452 ? { width: png.readUInt32BE(16), height: png.readUInt32BE(20) } : null;
}

export type DeviceKind = 'android' | 'ios';
export interface DeviceInfo {
  kind: DeviceKind;
  id: string;
  name: string;
}
type Failure = Fail<'bad_request' | 'unavailable'>;
const ID = /^[\w.:-]{1,80}$/;
const IDB_HOW = 'Taps and typing on an iOS simulator need idb: brew install facebook/fb/idb-companion && pip3 install fb-idb';

/** Booted simulators out of `simctl list devices booted -j`. */
export function bootedSims(json: string): DeviceInfo[] {
  const all = (JSON.parse(json) as { devices?: Record<string, { udid: string; name: string; state: string }[]> }).devices ?? {};
  return Object.values(all)
    .flat()
    .filter((d) => d.state === 'Booted')
    .map((d) => ({ kind: 'ios' as const, id: d.udid, name: d.name }));
}

/** Attached Android devices out of `adb devices`. */
export const adbDevices = (text: string): DeviceInfo[] =>
  [...text.matchAll(/^(\S+)\t(device)$/gm)].map((m) => ({ kind: 'android' as const, id: m[1]!, name: m[1]! }));

/** What `adb shell input text` takes: spaces are %s, and the whole is one quoted word for the device's shell. */
export const androidText = (text: string): string => `'${text.replace(/ /g, '%s').replace(/'/g, `'\\''`)}'`;

export class Devices {
  private origin: string;
  private exec: Run;
  private adb: string;
  private sizes = new Map<string, { width: number; height: number }>();
  private points = new Map<string, { width: number; height: number }>();
  constructor(origin: string, exec: Run = run, adb: string = adbPath()) {
    this.origin = origin;
    this.exec = exec;
    this.adb = adb;
  }

  /** The booted iOS simulators and the Android devices adb sees, and whether idb is there to drive a simulator. */
  async list(e: Sender): Promise<{ ok: true; devices: DeviceInfo[]; idb: boolean } | Failure> {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    const [ios, android, idb] = await Promise.all([
      this.exec('xcrun', ['simctl', 'list', 'devices', 'booted', '-j']).then(
        (o) => bootedSims(o.toString()),
        () => [],
      ),
      this.exec(this.adb, ['devices']).then(
        (o) => adbDevices(o.toString()),
        () => [],
      ),
      this.exec('idb', ['--help']).then(
        () => true,
        (err) => (err as NodeJS.ErrnoException).code !== 'ENOENT',
      ),
    ]);
    return { ok: true, devices: [...android, ...ios], idb };
  }

  /** The picture of the device now, a PNG (base64), or the sentence that says why there is none. */
  async shot(e: Sender, arg: unknown): Promise<{ ok: true; png: string } | Failure> {
    const t = this.target(e, arg);
    if (!t.ok) return t;
    try {
      const png = t.kind === 'android' ? await this.androidShot(t.id) : await this.iosShot(t.id);
      const size = pngSize(png);
      if (size) this.sizes.set(t.key, size);
      return { ok: true, png: png.toString('base64') };
    } catch (err) {
      return unavailable(t.kind === 'android' ? androidWhy(err) : iosWhy(err));
    }
  }

  /** A tap at a place in the last picture, as fractions of its width and height. */
  async tap(e: Sender, arg: unknown): Promise<{ ok: true } | Failure> {
    const t = this.target(e, arg);
    if (!t.ok) return t;
    const at = this.place(arg, 'x', 'y');
    if (!at) return bad('tap takes x and y between 0 and 1');
    return this.act(
      t,
      (px) => ['input', 'tap', String(px(at.x, 'width')), String(px(at.y, 'height'))],
      (pt) => ['ui', 'tap', pt(at.x, 'width'), pt(at.y, 'height')],
    );
  }

  /** A drag from one place of the last picture to another, as fractions, over `ms` milliseconds. */
  async swipe(e: Sender, arg: unknown): Promise<{ ok: true } | Failure> {
    const t = this.target(e, arg);
    if (!t.ok) return t;
    const from = this.place(arg, 'x1', 'y1');
    const to = this.place(arg, 'x2', 'y2');
    const ms = Math.min(2000, Math.max(50, Number((arg as { ms?: unknown }).ms) || 300));
    if (!from || !to) return bad('swipe takes x1, y1, x2 and y2 between 0 and 1');
    return this.act(
      t,
      (px) => [
        'input',
        'swipe',
        String(px(from.x, 'width')),
        String(px(from.y, 'height')),
        String(px(to.x, 'width')),
        String(px(to.y, 'height')),
        String(ms),
      ],
      (pt) => [
        'ui',
        'swipe',
        pt(from.x, 'width'),
        pt(from.y, 'height'),
        pt(to.x, 'width'),
        pt(to.y, 'height'),
        '--duration',
        String(ms / 1000),
      ],
    );
  }

  /** Typed text on the device. */
  async text(e: Sender, arg: unknown): Promise<{ ok: true } | Failure> {
    const t = this.target(e, arg);
    if (!t.ok) return t;
    const text = (arg as { text?: unknown }).text;
    if (typeof text !== 'string' || !text || text.length > 200 || /[\n\r]/.test(text))
      return bad('text is one line of at most 200 characters');
    return this.act(
      t,
      () => ['input', 'text', androidText(text)],
      () => ['ui', 'text', text],
    );
  }

  private target(e: Sender, arg: unknown): { ok: true; kind: DeviceKind; id: string; key: string } | Failure {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    const a = arg as { kind?: unknown; id?: unknown } | string | null;
    const kind = typeof a === 'string' ? a : a?.kind;
    const id = typeof a === 'string' ? '' : (a?.id ?? '');
    if (kind !== 'android' && kind !== 'ios') return bad('a device is "android" or "ios"');
    if (typeof id !== 'string' || (id && !ID.test(id))) return bad('a device id is a serial or a UDID');
    return { ok: true, kind, id, key: `${kind}:${id}` };
  }

  private place(arg: unknown, kx: string, ky: string): { x: number; y: number } | null {
    const a = arg as Record<string, unknown> | null;
    const x = a?.[kx];
    const y = a?.[ky];
    return typeof x === 'number' && typeof y === 'number' && x >= 0 && x <= 1 && y >= 0 && y <= 1 ? { x, y } : null;
  }

  /** Runs an action on the picked device: Android through adb in pixels of the last picture, iOS through idb in the simulator's points. */
  private async act(
    t: { kind: DeviceKind; id: string; key: string },
    android: (px: (f: number, side: 'width' | 'height') => number) => string[],
    ios: (pt: (f: number, side: 'width' | 'height') => string) => string[],
  ): Promise<{ ok: true } | Failure> {
    const size = this.sizes.get(t.key);
    if (!size) return unavailable('There is no picture of the device yet.');
    try {
      if (t.kind === 'android') {
        await this.exec(this.adb, [...(t.id ? ['-s', t.id] : []), 'shell', ...android((f, side) => Math.round(f * size[side]))]);
      } else {
        const pts = await this.pointsOf(t.id);
        await this.exec('idb', [...ios((f, side) => String(Math.round(f * pts[side]))), '--udid', t.id]);
      }
      return { ok: true };
    } catch (err) {
      if (t.kind === 'ios' && (err as NodeJS.ErrnoException).code === 'ENOENT') return unavailable(IDB_HOW);
      return unavailable(t.kind === 'android' ? androidWhy(err) : iosWhy(err));
    }
  }

  /** The simulator's screen in points, which idb takes, from `idb describe`. */
  private async pointsOf(udid: string): Promise<{ width: number; height: number }> {
    const known = this.points.get(udid);
    if (known) return known;
    const d = JSON.parse((await this.exec('idb', ['describe', '--udid', udid, '--json'])).toString()) as {
      screen_dimensions?: { width_points?: number; height_points?: number };
    };
    const w = d.screen_dimensions?.width_points;
    const h = d.screen_dimensions?.height_points;
    if (!w || !h) throw new Error('idb gave no screen size');
    this.points.set(udid, { width: w, height: h });
    return { width: w, height: h };
  }

  private async androidShot(id: string): Promise<Buffer> {
    const list = (await this.exec(this.adb, ['devices'])).toString();
    if (!/\tdevice$/m.test(list)) throw new Error('none');
    return this.exec(this.adb, [...(id ? ['-s', id] : []), 'exec-out', 'screencap', '-p']);
  }

  private async iosShot(id: string): Promise<Buffer> {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'handline-sim-'));
    try {
      const file = path.join(dir, 'shot.png');
      await this.exec('xcrun', ['simctl', 'io', id || 'booted', 'screenshot', '--type=png', file]);
      return fs.readFileSync(file);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  }
}

const androidWhy = (err: unknown): string =>
  (err as NodeJS.ErrnoException).code === 'ENOENT'
    ? 'adb is not installed here (looked in the Android SDK and on the PATH).'
    : 'No Android emulator or device is running. Start one and it shows here.';
const iosWhy = (err: unknown): string =>
  (err as NodeJS.ErrnoException).code === 'ENOENT'
    ? 'Xcode is not installed here, so there is no iOS simulator to show.'
    : 'No iOS simulator is booted. Boot one and it shows here.';
