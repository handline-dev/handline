// The sidebar's nav rows: the shape every row shares.

// One shape per nav row; the current one is raised with the inset bar. The arbitrary background sorts after `bg-transparent`,
// which a plain `bg-raised` here would lose to.
export const NAV = 'nav flex h-7 w-full items-center gap-2 rounded-ctl border-0 bg-transparent px-2 text-left text-ink tint';
export const navOf = (on: boolean): string => (on ? `${NAV} on [background:var(--bg-3)] shadow-[inset_2px_0_0_var(--ink)]` : NAV);
