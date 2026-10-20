// The web preview tab: a page of an agent's own app (a dev server) shown in a native view laid over the window's content, where the tab's placeholder is.
// The page says which address and where; the view has no preload, so what it loads gets nothing from the shell.
import { WebContentsView, session, type BrowserWindow } from 'electron';
import type { Fail } from '@handline/app-core';
import { fromWindow, type Sender } from './bridge.ts';

export interface PreviewArg {
  url: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

const bad = (message: string): Fail<'bad_request'> => ({ ok: false, code: 'bad_request', message });

/** The address and bounds the page sent, or the sentence that says what is wrong. Only http(s) addresses are shown. */
export function parsePreview(arg: unknown): PreviewArg | string {
  if (typeof arg !== 'object' || arg === null) return 'preview takes { url, x, y, width, height } or null';
  const a = arg as Record<string, unknown>;
  for (const k of ['x', 'y', 'width', 'height']) if (typeof a[k] !== 'number' || !Number.isFinite(a[k])) return `${k} is a number`;
  if (typeof a.url !== 'string' || a.url.length > 2048) return 'url is a string';
  try {
    if (!/^https?:$/.test(new URL(a.url).protocol)) return 'only http and https addresses are shown';
  } catch {
    return 'url is not an address';
  }
  return { url: a.url, x: a.x as number, y: a.y as number, width: a.width as number, height: a.height as number };
}

export class Preview {
  private view: WebContentsView | null = null;
  private shown = '';
  private origin: string;
  private window: () => BrowserWindow | null;
  constructor(origin: string, window: () => BrowserWindow | null) {
    this.origin = origin;
    this.window = window;
  }

  set(e: Sender, arg: unknown): { ok: true } | Fail<'bad_request'> {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    const w = this.window();
    if (arg === null) {
      this.close();
      return { ok: true };
    }
    const p = parsePreview(arg);
    if (typeof p === 'string') return bad(p);
    if (!w || w.isDestroyed()) return bad('no window');
    if (!this.view) {
      // Its own session, which grants the page no permission (camera, microphone, location...): Electron grants every one by default.
      const own = session.fromPartition('preview');
      own.setPermissionRequestHandler((_wc, _permission, grant) => grant(false));
      own.setPermissionCheckHandler(() => false);
      this.view = new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true, session: own } });
      w.contentView.addChildView(this.view);
      // A link that wants a new window stays out: the preview is one page.
      this.view.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    }
    this.view.setBounds({ x: Math.round(p.x), y: Math.round(p.y), width: Math.max(0, Math.round(p.width)), height: Math.max(0, Math.round(p.height)) });
    if (p.url !== this.shown) {
      this.shown = p.url;
      void this.view.webContents.loadURL(p.url).catch(() => undefined);
    }
    return { ok: true };
  }

  /** The page as drawn now, for the person to mark. */
  async capture(e: Sender): Promise<{ ok: true; png: string } | Fail<'bad_request' | 'unavailable'>> {
    if (!fromWindow(e, this.origin)) return bad('not from the app window');
    if (!this.view) return { ok: false, code: 'unavailable', message: 'The preview is not showing.' };
    const image = await this.view.webContents.capturePage();
    return image.isEmpty() ? { ok: false, code: 'unavailable', message: 'The preview is blank.' } : { ok: true, png: image.toPNG().toString('base64') };
  }

  close(): void {
    const w = this.window();
    if (this.view && w && !w.isDestroyed()) w.contentView.removeChildView(this.view);
    this.view?.webContents.close();
    this.view = null;
    this.shown = '';
  }
}
