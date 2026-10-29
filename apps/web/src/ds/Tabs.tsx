// Tabs of an agent's window. A task opened in passing reuses one slanted preview tab; touching it pins it.
import { Plus, X, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

export function Tabs({ owner, children }: { owner?: string; children: ReactNode }) {
  return (
    <div role="tablist" className="flex h-[var(--h-tabs)] items-center gap-0.5 border-b border-line px-2 text-sm">
      {owner ? <span className="mr-1.5 ml-0.5 font-semibold">{owner}</span> : null}
      {children}
    </div>
  );
}

export function Tab({
  icon: Glyph,
  active,
  preview,
  mono,
  onSelect,
  onClose,
  children,
}: {
  icon?: LucideIcon;
  active?: boolean;
  preview?: boolean;
  mono?: boolean;
  onSelect?: () => void;
  onClose?: () => void;
  children: ReactNode;
}) {
  return (
    <span
      className={`group inline-flex h-[var(--h-m)] items-center rounded-sm ${active ? 'bg-panel text-ink' : 'text-ink-2 hover:bg-surface'}`}
    >
      <button
        type="button"
        role="tab"
        aria-selected={!!active}
        onClick={onSelect}
        className={`inline-flex h-full cursor-pointer items-center gap-1.5 rounded-sm border-0 bg-transparent pl-2.5 text-inherit focus-visible:outline-2 focus-visible:outline-acc ${onClose ? 'pr-0.5' : 'pr-2.5'}${preview ? ' italic' : ''}${mono ? ' font-mono' : ''}`}
      >
        {Glyph ? <Glyph width={14} height={14} strokeWidth={1.75} aria-hidden /> : null}
        {children}
      </button>
      {onClose ? (
        <button
          type="button"
          aria-label="Close tab"
          onClick={onClose}
          className="mr-1 inline-flex h-5 w-5 cursor-pointer items-center justify-center rounded-sm border-0 bg-transparent text-inherit opacity-0 group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-acc"
        >
          <X width={12} height={12} strokeWidth={1.75} aria-hidden />
        </button>
      ) : null}
    </span>
  );
}

export function NewTab({ onClick }: { onClick?: () => void }) {
  return (
    <button
      type="button"
      aria-label="New tab"
      title="New tab"
      onClick={onClick}
      className="inline-flex h-[var(--h-s)] w-[var(--h-s)] cursor-pointer items-center justify-center rounded-sm border-0 bg-transparent text-ink-2 hover:bg-panel hover:text-ink focus-visible:outline-2 focus-visible:outline-acc"
    >
      <Plus width={14} height={14} strokeWidth={1.75} aria-hidden />
    </button>
  );
}
