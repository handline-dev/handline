// Window capture for the page: the list of windows with a small picture each, and one window as PNG (docs/story1-research.md, 1-1).
// Electron's desktopCapturer is injected, so this runs under node. The page never names a screen or a path, only a window id from the list.
import type { Fail } from '@handline/app-core';
import { CHANNELS, fromWindow, type Sender } from '#src/bridge.ts';

export interface Source {
  id: string;
  name: string;
  thumbnail: { isEmpty(): boolean; toPNG(): Buffer; toDataURL(): string };
}
export interface CaptureDeps {
  /** desktopCapturer.getSources for windows, each with a picture at most `size` px. */
  sources(size: { width: number; height: number }): Promise<Source[]>;
}
export type CaptureFail = Fail<'bad_request' | 'unavailable' | 'no_permission' | 'not_found' | 'too_large'>;
export type WindowsReply = { ok: true; windows: { id: string; name: string; thumb: string }[] } | CaptureFail;
export type TakeReply = { ok: true; name: string; png: string } | CaptureFail;

/** What an upload takes (contract x-attachments: an image is at most 10 MiB). */
export const MAX_PNG = 10 * 1024 * 1024;
const LIST_SIZE = { width: 320, height: 200 };
const FULL_SIZE = { width: 3840, height: 2400 };
const fail = (code: CaptureFail['code'], message: string): CaptureFail => ({ ok: false, code, message });
const NO_PERMISSION = 'The app may not record the screen: allow it in System Settings, Privacy & Security, Screen Recording.';

export class Capture {
  private origin: string;
  private deps: CaptureDeps;
  constructor(origin: string, deps: CaptureDeps) {
    this.origin = origin;
    this.deps = deps;
  }

  async windows(e: Sender): Promise<WindowsReply> {
    if (!fromWindow(e, this.origin)) return fail('bad_request', 'not from the app window');
    let all: Source[];
    try {
      all = await this.deps.sources(LIST_SIZE);
    } catch (err) {
      return fail('unavailable', err instanceof Error ? err.message : String(err));
    }
    // Without the permission the OS still lists windows but every picture is empty.
    if (all.length && all.every((s) => s.thumbnail.isEmpty())) return fail('no_permission', NO_PERMISSION);
    return {
      ok: true,
      windows: all.filter((s) => !s.thumbnail.isEmpty()).map((s) => ({ id: s.id, name: s.name, thumb: s.thumbnail.toDataURL() })),
    };
  }

  async take(e: Sender, arg: unknown): Promise<TakeReply> {
    if (!fromWindow(e, this.origin)) return fail('bad_request', 'not from the app window');
    const id = (arg as { id?: unknown } | null)?.id;
    if (typeof id !== 'string' || id.length > 256) return fail('bad_request', 'capture takes { id }');
    let all: Source[];
    try {
      // desktopCapturer cannot ask for one window: this is the one full-size call of the capture, made when the person has picked.
      all = await this.deps.sources(FULL_SIZE);
    } catch (err) {
      return fail('unavailable', err instanceof Error ? err.message : String(err));
    }
    const s = all.find((x) => x.id === id);
    if (!s) return fail('not_found', 'That window is gone.');
    if (s.thumbnail.isEmpty()) return fail('no_permission', NO_PERMISSION);
    const png = s.thumbnail.toPNG();
    if (png.length > MAX_PNG) return fail('too_large', 'The picture is over 10 MB: make the window smaller.');
    return {
      ok: true,
      name: `${
        s.name
          .replace(/[^\w .-]+/g, '')
          .replace(/^[. ]+/, '')
          .trim()
          .slice(0, 60) || 'window'
      }.png`,
      png: png.toString('base64'),
    };
  }
}

/** Serves the two channels on Electron's ipcMain. */
export function serveCapture(
  ipc: { handle(channel: string, f: (e: Sender, arg: unknown) => unknown): void },
  origin: string,
  deps: CaptureDeps,
): void {
  const c = new Capture(origin, deps);
  ipc.handle(CHANNELS.captureWindows, (e) => c.windows(e));
  ipc.handle(CHANNELS.captureWindow, (e, arg) => c.take(e, arg));
}
