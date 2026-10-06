// The desktop shell's calls (docs/electron-bridge.md). This is the only file that reads `window.handline`; in a plain browser there
// is none, every function here does nothing, and the page works as it did.
import { useEffect, useRef } from 'react';
import { routeOfTarget, type ShellApi } from '@handline/app-core';
import { useBusy } from '@handline/app-data';

type Call = 'screen' | 'unsent' | 'onNavigate';

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
        const route = routeOfTarget(to, to.event);
        if (route) location.hash = route;
      }),
    [],
  );
}
