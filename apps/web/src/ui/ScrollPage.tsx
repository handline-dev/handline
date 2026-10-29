// The one page layout: a scrolling section at one max width, its header line (title, then actions, sticky) and its title. Every page is built on these.
import type { ComponentProps } from 'react';
import { createPortal } from 'react-dom';
import { Button } from '#src/ui/Button.tsx';
import { usePageBarSlot } from '#src/ui/TopBar.tsx';

const join = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');

// `beside` is for a page that shares the row with a pane: it takes the room the pane leaves, not the page width.
export function ScrollPage({ className, beside, ...rest }: ComponentProps<'section'> & { beside?: boolean }) {
  const size = beside ? 'min-w-0 flex-1' : 'mx-auto w-full max-w-[var(--w-page)]';
  return <section {...rest} className={join('h-full overflow-auto px-4 pt-0 pb-6', size, className)} />;
}

/**
 * The page's title and tools. Inside the shell they are moved into the top bar (the PageBar), so no page carries a head of its own;
 * `back` puts the way up one level before the title: every sub-page leaves the same way, from the same place.
 */
export function PageHead({ className, back, children, ...rest }: ComponentProps<'header'> & { back?: { label: string; to: () => void } }) {
  const slot = usePageBarSlot();
  if (slot === null) return null;
  const head = (
    <header
      {...rest}
      className={join(
        'flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1',
        slot ? '' : 'sticky top-0 z-1 bg-canvas pt-4 pb-3',
        className,
      )}
    >
      {back ? (
        <Button className="back -ml-2 text-ink-2" size="sm" icon="back" label={`Back to ${back.label}`} onClick={back.to}>
          {back.label}
        </Button>
      ) : null}
      {children}
    </header>
  );
  return slot ? createPortal(head, slot) : head;
}

export function PageTitle({ className, ...rest }: ComponentProps<'h2'>) {
  return <h2 {...rest} className={join('m-0 text-xl font-semibold text-ink', className)} />;
}
