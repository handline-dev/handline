// A section heading: `caps` is the flat caps label with its icon on a settings page, `small` the small-caps heading of a card list.
import type { LucideIcon } from 'lucide-react';
import type { ComponentProps } from 'react';

const LOOK = {
  caps: 'mx-0 mt-0 mb-1 flex items-center gap-2 text-md font-semibold text-ink [&_svg]:text-ink-2',
  small: 'mt-[18px] mb-1.5 text-[11px] tracking-[0.06em] text-ink-2 [font-variant:small-caps]',
} as const;

export function SectionHead({
  variant = 'caps',
  icon: Glyph,
  className,
  children,
  ...rest
}: ComponentProps<'h3'> & { variant?: keyof typeof LOOK; icon?: LucideIcon }) {
  return (
    <h3 {...rest} className={`shead ${LOOK[variant]}${className ? ` ${className}` : ''}`}>
      {Glyph ? <Glyph width={16} height={16} strokeWidth={1.75} aria-hidden /> : null}
      {children}
    </h3>
  );
}
