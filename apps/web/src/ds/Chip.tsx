// A chip sticks one fact on a thing and is never a button. A count shows only when it is more than zero.
import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

export type ChipKind = 'source' | 'you' | 'unread' | 'count';

const KIND: Record<ChipKind, string> = {
  source: 'border border-line-strong text-ink-2',
  you: 'bg-wait font-medium text-white',
  unread: 'bg-acc font-semibold text-white',
  count: 'min-w-[18px] justify-center bg-panel font-semibold text-ink',
};

export function Chip({ kind = 'source', icon: Glyph, children }: { kind?: ChipKind; icon?: LucideIcon; children?: ReactNode }) {
  return (
    <span className={`inline-flex h-5 items-center gap-1 rounded-[5px] px-1.5 text-xs whitespace-nowrap ${KIND[kind]}`}>
      {Glyph ? <Glyph width={12} height={12} strokeWidth={1.75} aria-hidden /> : null}
      {children}
    </span>
  );
}
