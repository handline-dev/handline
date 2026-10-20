// The Android and iOS tabs: a picture of a running Android emulator or device (adb) or iOS simulator (simctl), and taps on the Android one.
// The page only names which of the two; the commands and their arguments are fixed here.
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Fail } from '@handline/app-core';
import { fromWindow, type Sender } from './bridge.ts';

type Run = (file: string, args: string[]) => Promise<Buffer>;
const run: Run = (file, args) =>
  new Promise((resolve, reject) =>
    execFile(file, args, { encoding: 'buffer', maxBuffer: 64 * 1024 * 1024, timeout: 10_000 }, (err, out) => (err ? reject(err) : resolve(out))),
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

export class Devices {
  private origin: string;
  private exec: Run;
  private adb: string;
  private size: { width: number; height: number } | null = null;
  constructor(origin: string, exec: Run = run, adb: string = adbPath()) {
    this.origin = origin;
    this.exec = exec;
    this.adb = adb;
  }

  /** The picture of the device now, a PNG (base64), or the sentence that says why there is none. */
  async shot(e: Sender, arg: unknown): Promise<{ ok: true; png: string } | Fail<'bad_request' | 'unavailable'>> {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    if (arg !== 'android' && arg !== 'ios') return bad('deviceShot takes "android" or "ios"');
    try {
      const png = arg === 'android' ? await this.androidShot() : await this.iosShot();
      this.size = arg === 'android' ? pngSize(png) : this.size;
      return { ok: true, png: png.toString('base64') };
    } catch (err) {
      return unavailable(arg === 'android' ? androidWhy(err) : iosWhy(err));
    }
  }

  /** A tap at a place in the last Android picture, as fractions of its width and height. */
  async tap(e: Sender, arg: unknown): Promise<{ ok: true } | Fail<'bad_request' | 'unavailable'>> {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    const a = arg as { x?: unknown; y?: unknown } | null;
    if (typeof a?.x !== 'number' || typeof a.y !== 'number' || a.x < 0 || a.x > 1 || a.y < 0 || a.y > 1) return bad('tap takes x and y between 0 and 1');
    if (!this.size) return unavailable('There is no picture of the device yet.');
    try {
      await this.exec(this.adb, ['shell', 'input', 'tap', String(Math.round(a.x * this.size.width)), String(Math.round(a.y * this.size.height))]);
      return { ok: true };
    } catch (err) {
      return unavailable(androidWhy(err));
    }
  }

  private async androidShot(): Promise<Buffer> {
    const list = (await this.exec(this.adb, ['devices'])).toString();
    if (!/\tdevice$/m.test(list)) throw new Error('none');
    return this.exec(this.adb, ['exec-out', 'screencap', '-p']);
  }

  private async iosShot(): Promise<Buffer> {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'handline-sim-'));
    try {
      const file = path.join(dir, 'shot.png');
      await this.exec('xcrun', ['simctl', 'io', 'booted', 'screenshot', '--type=png', file]);
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
