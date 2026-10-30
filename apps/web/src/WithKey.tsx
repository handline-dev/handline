// A control with its key shown under it while ⌘ is held (heldKeys.ts; `.ui-keys` is hidden otherwise).
import type { ReactNode } from 'react';

export function WithKey({ k, children }: { k: string; children: ReactNode }) {
  return (
    <span className="relative inline-flex">
      {children}
      <kbd className="ui-keys pointer-events-none absolute bottom-0 left-1/2 z-30 -translate-x-1/2 rounded-sm bg-ink px-1 font-sans text-[10px] whitespace-nowrap text-canvas">
        {k}
      </kbd>
    </span>
  );
}
