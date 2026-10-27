// The one top bar: always there, 44px, over the page area. It carries the rail's buttons while the rail is folded, then a slot
// (the PageBar) that each page fills with its title and tools by rendering a PageHead.
import { createContext, useContext, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** `undefined`: no shell around (a page on its own renders its head inline); `null`: the bar is there but its slot is not mounted yet. */
export const PageBarSlot = createContext<HTMLElement | null | undefined>(undefined);
export const usePageBarSlot = () => useContext(PageBarSlot);

/** The frame of the page area: the bar, then the page under it. `lead` is the rail's buttons while the rail is folded. */
export function Shell({ lead, children }: { lead?: ReactNode; children: ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  return (
    <PageBarSlot.Provider value={slot}>
      <div className="shell-main flex min-h-0 min-w-0 flex-1 flex-col">
        <TopBar lead={lead} slot={setSlot} />
        {children}
      </div>
    </PageBarSlot.Provider>
  );
}

function TopBar({ lead, slot }: { lead?: ReactNode; slot: (el: HTMLElement | null) => void }) {
  return (
    <div
      className="topbar flex min-h-[var(--h-top)] flex-none items-center gap-2 border-b border-line bg-canvas px-4 py-1"
      data-lead={lead ? '' : undefined}
    >
      {lead ? <div className="topbar-lead flex flex-none items-center shell:pl-[var(--lights-w,86px)] -ml-2">{lead}</div> : null}
      <div className="page-bar flex min-w-0 flex-1 items-center" ref={slot} />
    </div>
  );
}

/**
 * The bar of a page that has more than a title: the name, then `tabs` taking the free width, then `tools`. It lands in the bar's slot; with no
 * shell around it stays where it is rendered.
 */
export function PageBar({ title, tabs, tools }: { title: ReactNode; tabs?: ReactNode; tools?: ReactNode }) {
  const slot = usePageBarSlot();
  if (slot === null) return null;
  const bar = (
    <div className="flex min-h-8 min-w-0 flex-1 items-center gap-2">
      <h2
        className="m-0 max-w-40 flex-none truncate text-page font-semibold text-ink"
        title={typeof title === 'string' ? title : undefined}
      >
        {title}
      </h2>
      <div className="flex min-w-0 flex-1 items-center">{tabs}</div>
      <div className="flex flex-none items-center gap-0.5">{tools}</div>
    </div>
  );
  return slot ? createPortal(bar, slot) : bar;
}
