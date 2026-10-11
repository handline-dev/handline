// A section heading: `caps` is the flat caps label with its icon on a settings page, `small` the small-caps heading of a card list.
import type { ComponentProps } from 'react';

const LOOK = {
  caps: 'mx-0 mt-0 mb-1.5 flex items-center gap-1.5 text-hint font-normal tracking-[0.04em] text-ink-3 uppercase',
  small: 'mt-[18px] mb-1.5 text-[11px] tracking-[0.06em] text-ink-2 [font-variant:small-caps]',
} as const;

export function SectionHead({ variant = 'caps', className, ...rest }: ComponentProps<'h3'> & { variant?: keyof typeof LOOK }) {
  return <h3 {...rest} className={`shead ${LOOK[variant]}${className ? ` ${className}` : ''}`} />;
}
