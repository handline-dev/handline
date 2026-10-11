import type { Badge } from '@handline/app-core';

// The border colour per kind (never two colours on one element — css order, not class order, would win).
const LOOK: Record<Badge['kind'], string> = {
  dialog: 'border-wait font-semibold text-wait',
  stalled: 'border-bad text-bad',
  you: 'border-primary text-link',
  no_terminal: 'border-dashed border-line',
  other: 'border-line',
  held: 'border-line',
  no_session: 'border-line',
};

/** The badges of an agent: the dialog and stall ones are the loud ones. A click handler per kind is the caller's. */
export function Badges({ badges, onClick }: { badges: Badge[]; onClick?: Partial<Record<Badge['kind'], () => void>> }) {
  return (
    <>
      {badges.map((b) => {
        const clickable = !!onClick?.[b.kind];
        return (
          <span
            key={b.kind}
            className={`badge ${b.kind} mr-1.5 inline-block rounded-card border px-1.5 text-hint text-ink-2 ${LOOK[b.kind]}${clickable ? ' cursor-pointer' : ''}`}
            title={b.tip}
            role={clickable ? 'button' : undefined}
            onClick={(e) => {
              const f = onClick?.[b.kind];
              if (!f) return;
              e.stopPropagation(); // the badge acts; the tab or row under it does not also select
              f();
            }}
          >
            {b.label}
          </span>
        );
      })}
    </>
  );
}
