// The sidebar's nav rows that are more than a link: the shape every row shares, and Pair a phone.
import { useEffect, useState } from 'react';
import { shell } from './shell.ts';

// One shape per nav row; the current one is raised with the inset bar. The arbitrary background sorts after `bg-transparent`,
// which a plain `bg-raised` here would lose to.
export const NAV = 'nav flex h-7 w-full items-center gap-2 rounded-ctl border-0 bg-transparent px-2 text-left text-ink hover:bg-raised';
export const navOf = (on: boolean): string => (on ? `${NAV} on [background:var(--bg-3)] shadow-[inset_2px_0_0_var(--ink)]` : NAV);

/** Desktop app only, and only when it has its own gateway: `run` opens the one-time QR window a phone scans; `error` is why it did not. */
export function usePairPhone() {
  const [can, setCan] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    void shell('mode')
      ?.mode?.()
      .then((r) => setCan(r.ok && r.phone && !!shell('phone')));
  }, []);
  return { can, error, run: () => void shell('phone')!.phone!().then((r) => setError(r.ok ? '' : r.message)) };
}
