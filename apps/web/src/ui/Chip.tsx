// An icon-and-word item in a row: `ico` is a chat-strip chip (a link or span that turns bad when `data-lapsed`), `cell` a status-bar cell.
import type { ComponentProps, ElementType } from 'react';

export function Chip({
  variant = 'ico',
  as: Tag = 'span',
  className,
  ...rest
}: ComponentProps<'span'> & { variant?: 'ico' | 'cell'; as?: ElementType; href?: string }) {
  const look = variant === 'ico' ? 'chip-ico whitespace-nowrap text-inherit no-underline data-[lapsed]:text-bad' : 'status-cell';
  return <Tag {...rest} className={`${look} inline-flex items-center gap-1${className ? ` ${className}` : ''}`} />;
}
