// The one page layout: a scrolling section at one max width, its header line (title, then actions) and its title. Every page is built on these.
import type { ComponentProps } from 'react';

const join = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');

export function ScrollPage({ className, ...rest }: ComponentProps<'section'>) {
  return <section {...rest} className={join('mx-auto h-full w-full max-w-[var(--w-page)] overflow-auto px-4 pt-0 pb-6', className)} />;
}

export function PageHead({ className, ...rest }: ComponentProps<'header'>) {
  return <header {...rest} className={join('flex flex-wrap items-center gap-x-3 gap-y-2 pt-4 pb-3', className)} />;
}

export function PageTitle({ className, ...rest }: ComponentProps<'h2'>) {
  return <h2 {...rest} className={join('m-0 text-page font-semibold text-ink', className)} />;
}
