// The one page layout: a scrolling section at one max width, its header line (title, then actions, sticky) and its title. Every page is built on these.
import type { ComponentProps } from 'react';

const join = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');

// `beside` is for a page that shares the row with a pane: it takes the room the pane leaves, not the page width.
export function ScrollPage({ className, beside, ...rest }: ComponentProps<'section'> & { beside?: boolean }) {
  const size = beside ? 'min-w-0 flex-1' : 'mx-auto w-full max-w-[var(--w-page)]';
  return <section {...rest} className={join('h-full overflow-auto px-4 pt-0 pb-6', size, className)} />;
}

export function PageHead({ className, ...rest }: ComponentProps<'header'>) {
  return <header {...rest} className={join('sticky top-0 z-1 flex flex-wrap items-center gap-x-3 gap-y-2 bg-canvas pt-4 pb-3', className)} />;
}

export function PageTitle({ className, ...rest }: ComponentProps<'h2'>) {
  return <h2 {...rest} className={join('m-0 text-page font-semibold text-ink', className)} />;
}
