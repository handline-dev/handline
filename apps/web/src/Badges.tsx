import { CircleAlert, MessageCircleQuestion } from 'lucide-react';
import type { Badge } from '@handline/app-core';

// The border colour per kind (never two colours on one element — css order, not class order, would win).
const LOOK: Record<Badge['kind'], string> = {
  dialog: 'border-wait font-semibold text-wait',
  stalled: 'border-bad text-bad',
  no_terminal: 'border-dashed border-line',
  other: 'border-line',
  held: 'border-line',
  no_session: 'border-line',
};

/** The badges of an agent: the dialog and stall ones are the loud ones. A click handler per kind is the caller's. */
export function Badges({
  badges,
  onClick,
  mark,
}: {
  badges: Badge[];
  onClick?: Partial<Record<Badge['kind'], () => void>>;
  /** A glyph with the badge's words as its label, not a pill: a list row already shows the state in words. */
  mark?: boolean;
}) {
  return (
    <>
      {badges
        .filter((b) => b.kind !== 'no_terminal')
        .map((b) => {
          if (mark) {
            const Glyph = b.kind === 'dialog' ? MessageCircleQuestion : CircleAlert;
            return (
              <span
                key={b.kind}
                className={`badge ${b.kind} inline-flex flex-none ${b.kind === 'dialog' ? 'text-wait' : 'text-bad'}`}
                title={b.tip}
                role="img"
                aria-label={b.tip ?? b.label}
              >
                <Glyph width={14} height={14} strokeWidth={1.75} aria-hidden />
              </span>
            );
          }
          const clickable = !!onClick?.[b.kind];
          return (
            <span
              key={b.kind}
              className={`badge ${b.kind} mr-1.5 inline-block flex-none whitespace-nowrap rounded-md border px-1.5 text-xs text-ink-2 ${LOOK[b.kind]}${clickable ? ' cursor-pointer' : ''}`}
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
