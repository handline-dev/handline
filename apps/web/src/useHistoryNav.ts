// Whether Back and Forward have anywhere to go, from the Navigation API (Chromium, so the desktop shell). Without it both show.
import { useEffect, useState } from 'react';

interface Nav extends EventTarget {
  canGoBack: boolean;
  canGoForward: boolean;
}
const nav = (): Nav | null => (globalThis as { navigation?: Nav }).navigation ?? null;

export function useHistoryNav(): { back: boolean; forward: boolean } {
  const read = () => ({ back: nav()?.canGoBack ?? true, forward: nav()?.canGoForward ?? true });
  const [v, setV] = useState(read);
  useEffect(() => {
    const n = nav();
    if (!n) return;
    const sync = () => setV(read());
    n.addEventListener('currententrychange', sync);
    sync();
    return () => n.removeEventListener('currententrychange', sync);
  }, []);
  return v;
}
