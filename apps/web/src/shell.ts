// The desktop shell's calls (docs/electron-bridge.md). This is the only file that reads `window.handline`; in a plain browser there
// is none, every function here does nothing, and the page works as it did.
import { useEffect, useRef } from 'react';
import { routeOfTarget, type ShellApi } from '@handline/app-core';
import { useBusy, useUnread } from '@handline/app-data';
import { RpcError, type ClientOptions } from '@handline/client';

type Call =
  | 'zoom'
  | 'onFullScreen'
  | 'screen'
  | 'unsent'
  | 'onNavigate'
  | 'openPath'
  | 'compose'
  | 'cancelCompose'
  | 'mode'
  | 'setMode'
  | 'pairRemote'
  | 'phone'
  | 'preview'
  | 'capturePreview'
  | 'deviceShot'
  | 'deviceTap'
  | 'captureWindows'
  | 'captureWindow'
  | 'pathOf'
  | 'settingsGet'
  | 'settingsSet';

/** True in the desktop shell; main.tsx marks <html data-shell=desktop> from it. */
export const inShell = (): boolean => typeof window !== 'undefined' && 'handline' in window;

/**
 * The traffic lights are drawn by the OS at fixed pixels, so the rows beside them must not follow the page zoom, and in full screen
 * they are gone (the shell says when: onFullScreen). Sets --h-tabs (the row the lights are centred on, 32 screen px) and --lights-w
 * (the room they take, little in full screen). The zoom is the shell's own (a plain window: its outer width over the page's inner width).
 */
export function trackShellInsets(): void {
  const root = document.documentElement.style;
  let full = false;
  const set = () => {
    const zoom = shell('zoom')?.zoom?.() || (innerWidth ? outerWidth / innerWidth : 1);
    root.setProperty('--h-tabs', `${32 / zoom}px`);
    root.setProperty('--lights-w', `${(full ? 4 : 86) / zoom}px`);
  };
  // The shell's zoom can still be the old one while the resize it causes is delivered, so it is read again once that has passed.
  let later = 0;
  const settle = () => {
    set();
    clearTimeout(later);
    later = window.setTimeout(set, 150);
  };
  settle();
  addEventListener('resize', settle);
  shell('onFullScreen')?.onFullScreen?.((on) => {
    full = on;
    settle();
  });
}

/** The shell's API if this shell has `call` (a call is checked before it is used, never a version). */
export function shell(call: Call): ShellApi | null {
  const s = (window as { handline?: Partial<ShellApi> }).handline;
  return s?.api === 1 && typeof s[call] === 'function' ? (s as ShellApi) : null;
}

/** Something in the page a restart would lose: a box is open, a field holds text, or a send is in flight (a row the daemon has not numbered). */
export function holdsUnsent(doc: Document): boolean {
  if (doc.querySelector('dialog[open], [aria-modal="true"]')) return true;
  for (const f of Array.from(
    doc.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('textarea, input[type="text"], input:not([type])'),
  ))
    if (f.value.trim()) return true;
  return !!doc.querySelector('[data-id^="-"], [data-pending]');
}

/** Tells the shell which agent is on screen and whether a restart would lose something, and follows a clicked notice. */
export function useShell(agent: string): void {
  const busy = useBusy();
  const last = useRef<boolean | null>(null);
  const unread = useUnread();
  const unreadNow = useRef(unread);
  unreadNow.current = unread;
  useEffect(() => {
    void shell('screen')?.screen({ agent: agent || null });
  }, [agent]);
  useEffect(() => {
    const api = shell('unsent');
    if (!api) return;
    const tell = () => {
      const on = busy || holdsUnsent(document);
      if (on === last.current) return;
      last.current = on;
      void api.unsent(on);
    };
    tell();
    const watch = new MutationObserver(tell);
    watch.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['open', 'data-pending', 'data-id'],
    });
    document.addEventListener('input', tell, true);
    return () => {
      watch.disconnect();
      document.removeEventListener('input', tell, true);
    };
  }, [busy]);
  useEffect(
    () =>
      shell('onNavigate')?.onNavigate((to) => {
        const route = routeOfTarget(to, to.event, unreadNow.current);
        if (route) location.hash = route;
      }),
    [],
  );
}

/**
 * settings.get and settings.set through the shell, when it has them: the window connects as a paired device, which the daemon refuses
 * these two, so the shell's own connection answers. A refusal comes back as the error a daemon refusal is.
 */
export function settingsRelay(): ClientOptions['relay'] {
  const api = shell('settingsGet') && shell('settingsSet');
  if (!api) return undefined;
  const open = <T>(r: { ok: true; result: T } | { ok: false; code: string; message: string }): T => {
    if (r.ok) return r.result;
    throw new RpcError(r.code === 'refused' || r.code === 'bad_request' ? r.code : 'internal', r.message);
  };
  return {
    'settings.get': async (p) => open(await api.settingsGet!(p)),
    'settings.set': async (p) => open(await api.settingsSet!({ changes: p.changes, agent: p.agent })),
  };
}
