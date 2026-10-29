// An icon-and-word item in a row: `ico` is a chat-strip chip (a link or span that turns bad when `data-lapsed`), `cell` a status-bar cell.
import type { ComponentProps, ElementType } from 'react';

/** The size and padding every item of the chat strip shares, whatever it is made of (a menu button, a link, a plain chip). */
export const STRIP_ITEM = 'h-5 !px-1.5';

export function Chip({
  variant = 'ico',
  as: Tag = 'span',
  className,
  ...rest
}: ComponentProps<'span'> & { variant?: 'ico' | 'cell'; as?: ElementType; href?: string }) {
  const look =
    variant === 'ico'
      ? `chip-ico ${STRIP_ITEM} rounded-sm whitespace-nowrap text-inherit no-underline data-[lapsed]:text-bad`
      : 'status-cell';
  // A cell that does something takes the one tint; a plain span is text and takes none.
  const acts = Tag !== 'span' ? (variant === 'cell' ? ' tint -mx-1 rounded-sm px-1 text-inherit' : ' tint') : '';
  return <Tag {...rest} className={`${look}${acts} inline-flex items-center gap-1${className ? ` ${className}` : ''}`} />;
}
