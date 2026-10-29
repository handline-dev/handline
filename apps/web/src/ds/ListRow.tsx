// A list: a group head and rows. A row only opens (answers come after it is open); hover tints, selected fills one step darker, no box and no side stripe.
import type { ReactNode } from 'react';

export function SectionHead({ icon, count, children }: { icon?: ReactNode; count?: number; children: ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 px-3 pt-3 pb-1 text-sm font-semibold">
      {icon}
      {children}
      {count ? <span className="font-medium text-ink-3">{count}</span> : null}
    </div>
  );
}

export function ListRow({
  title,
  lead,
  chip,
  time,
  selected,
  done,
  onOpen,
}: {
  title: ReactNode;
  lead?: ReactNode;
  chip?: ReactNode;
  time?: string;
  selected?: boolean;
  done?: boolean;
  onOpen?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={selected ? 'true' : undefined}
      className={`flex h-9 w-full cursor-pointer items-center gap-3 rounded-sm border-0 px-3 text-left text-md focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-acc ${selected ? 'bg-panel' : 'bg-transparent hover:bg-surface'}`}
    >
      {lead}
      <span className={`min-w-0 flex-1 truncate ${done ? 'text-ink-3' : ''}`}>{title}</span>
      {chip}
      <span className="w-[2.4em] text-right text-xs text-ink-3">{time}</span>
    </button>
  );
}

export function EmptyList({ children }: { children: ReactNode }) {
  return <div className="px-0 py-8 text-center text-sm text-ink-3">{children}</div>;
}
