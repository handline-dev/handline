// The sidebar's nav rows: the shape every row shares. The current one is filled one step lighter, with no stripe.
const SHAPE =
  'nav flex h-[var(--h-m)] w-full cursor-pointer items-center gap-2 rounded-sm border-0 px-2 text-left text-md text-ink focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-acc';
export const NAV = `${SHAPE} bg-transparent tint`;
export const navOf = (on: boolean): string => (on ? `${SHAPE} on bg-canvas` : NAV);
