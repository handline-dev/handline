import type { Badge } from '@handline/app-core';

/** The badges of an agent: the dialog and stall ones are the loud ones. A click handler per kind is the caller's. */
export function Badges({ badges, onClick }: { badges: Badge[]; onClick?: Partial<Record<Badge['kind'], () => void>> }) {
  return (
    <>
      {badges.map((b) => (
        <span
          key={b.kind}
          className={`badge ${b.kind}`}
          title={b.tip}
          role={onClick?.[b.kind] ? 'button' : undefined}
          onClick={(e) => {
            const f = onClick?.[b.kind];
            if (!f) return;
            e.stopPropagation(); // the badge acts; the tab or row under it does not also select
            f();
          }}
        >
          {b.label}
        </span>
      ))}
    </>
  );
}
