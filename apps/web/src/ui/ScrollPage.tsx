// The one page layout: a scrolling section at one max width, its header line (title, then actions, sticky) and its title. Every page is built on these.
import type { ComponentProps } from 'react';
import { Button } from '#src/ui/Button.tsx';

const join = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');

// `beside` is for a page that shares the row with a pane: it takes the room the pane leaves, not the page width.
export function ScrollPage({ className, beside, ...rest }: ComponentProps<'section'> & { beside?: boolean }) {
  const size = beside ? 'min-w-0 flex-1' : 'mx-auto w-full max-w-[var(--w-page)]';
  return <section {...rest} className={join('h-full overflow-auto px-4 pt-0 pb-6', size, className)} />;
}

/** `back` puts the way up one level on a line of its own above the title: every sub-page leaves the same way, from the same place. */
export function PageHead({ className, back, children, ...rest }: ComponentProps<'header'> & { back?: { label: string; to: () => void } }) {
  return (
    <header {...rest} className={join('sticky top-0 z-1 flex flex-wrap items-center gap-x-3 gap-y-2 bg-canvas pt-4 pb-3', className)}>
      {back ? (
        <div className="basis-full">
          <Button className="back -ml-2 text-ink-2" size="sm" icon="back" label={`Back to ${back.label}`} onClick={back.to}>
            {back.label}
          </Button>
        </div>
      ) : null}
      {children}
    </header>
  );
}

export function PageTitle({ className, ...rest }: ComponentProps<'h2'>) {
  return <h2 {...rest} className={join('m-0 text-page font-semibold text-ink', className)} />;
}
