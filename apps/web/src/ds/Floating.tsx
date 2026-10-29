// The floating layers, the only parts with a shadow: a menu of what you DO to the thing, a popover, and a toast.
import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

/** A menu or popover surface; `children` are PopItems, a separator, or a small panel. */
export function Pop({ children, role = 'menu', className }: { children: ReactNode; role?: 'menu' | 'dialog'; className?: string }) {
  return (
    <div
      role={role}
      className={`min-w-[200px] rounded-md border border-line bg-canvas p-1 text-sm shadow-[var(--shadow)]${className ? ` ${className}` : ''}`}
    >
      {children}
    </div>
  );
}

export function PopItem({
  icon: Glyph,
  danger,
  onClick,
  children,
}: {
  icon?: LucideIcon;
  danger?: boolean;
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={`flex h-[30px] w-full cursor-pointer items-center gap-2 rounded-sm border-0 bg-transparent px-2.5 text-left hover:bg-panel focus-visible:bg-panel focus-visible:outline-none ${danger ? 'text-bad' : 'text-ink'}`}
    >
      {Glyph ? <Glyph width={14} height={14} strokeWidth={1.75} aria-hidden /> : null}
      {children}
    </button>
  );
}

export const PopSeparator = () => <hr className="my-1 border-0 border-t border-line" />;

/** A line of what just happened, with at most one link (Undo). */
export function Toast({ children, action, onAction }: { children: ReactNode; action?: string; onAction?: () => void }) {
  return (
    <div
      role="status"
      className="inline-flex items-center gap-3 rounded-md bg-ink px-3.5 py-[9px] text-sm text-canvas shadow-[var(--shadow)]"
    >
      <span>{children}</span>
      {action ? (
        <button
          type="button"
          onClick={onAction}
          className="cursor-pointer border-0 bg-transparent p-0 font-semibold text-[#9cb8ff] hover:underline focus-visible:outline-2 focus-visible:outline-[#9cb8ff]"
        >
          {action}
        </button>
      ) : null}
    </div>
  );
}
