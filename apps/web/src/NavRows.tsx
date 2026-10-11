// The sidebar's nav rows that are more than a link: the shape every row shares, and Pair a phone.
import { useEffect, useState } from 'react';
import { Icon } from './ui/index.ts';
import { shell } from './shell.ts';

// One shape per nav row; the current one is raised with the inset bar. The arbitrary background sorts after `bg-transparent`,
// which a plain `bg-raised` here would lose to.
export const NAV = 'nav flex h-7 w-full items-center gap-2 rounded-ctl border-0 bg-transparent px-2 text-left text-ink hover:bg-raised';
export const navOf = (on: boolean): string => (on ? `${NAV} on [background:var(--bg-3)] shadow-[inset_2px_0_0_var(--ink)]` : NAV);

/** Desktop app only, and only when it has its own gateway: opens the one-time QR window a phone scans. */
export function PairPhone() {
  const [can, setCan] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    void shell('mode')
      ?.mode?.()
      .then((r) => setCan(r.ok && r.phone && !!shell('phone')));
  }, []);
  if (!can) return null;
  return (
    <>
      <button className={NAV} onClick={() => void shell('phone')!.phone!().then((r) => setError(r.ok ? '' : r.message))}>
        <Icon name="open" size={14} aria-hidden /> Pair a phone
      </button>
      {error && (
        <p className="m-0 basis-full px-2 text-hint text-bad" role="alert">
          {error}
        </p>
      )}
    </>
  );
}
