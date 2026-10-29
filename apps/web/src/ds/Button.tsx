// The button: one primary per place, the rest quiet. Three heights; an icon-only button is a square and always carries its label.
import type { LucideIcon } from 'lucide-react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type ButtonKind = 'primary' | 'secondary' | 'quiet' | 'danger';
export type ButtonSize = 's' | 'm' | 'l';

const KIND: Record<ButtonKind, string> = {
  primary: 'border-acc bg-acc text-on-acc tint',
  secondary: 'border-line-strong bg-canvas text-ink tint',
  quiet: 'border-transparent bg-transparent text-ink-2 tint',
  danger: 'border-line-strong bg-canvas text-bad tint',
};
const SIZE: Record<ButtonSize, string> = {
  s: 'h-[var(--h-s)] px-[9px] text-xs',
  m: 'h-[var(--h-m)] px-3 text-sm',
  l: 'h-[var(--h-l)] px-4 text-md',
};
const SQUARE: Record<ButtonSize, string> = { s: 'w-[var(--h-s)] !px-0', m: 'w-[var(--h-m)] !px-0', l: 'w-[var(--h-l)] !px-0' };

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  kind?: ButtonKind;
  size?: ButtonSize;
  icon?: LucideIcon;
  /** The words of the button; on an icon-only button the tooltip and aria-label. */
  label: string;
  /** Icon-only: draw the icon alone. */
  iconOnly?: boolean;
  children?: ReactNode;
}

export function Button({
  kind = 'secondary',
  size = 'm',
  icon: Glyph,
  label,
  iconOnly,
  children,
  className,
  type = 'button',
  ...rest
}: ButtonProps) {
  const px = size === 's' ? 13 : 15;
  return (
    <button
      {...rest}
      type={type}
      title={iconOnly ? label : rest.title}
      aria-label={iconOnly ? label : rest['aria-label']}
      className={`inline-flex items-center justify-center gap-1.5 rounded-sm border font-sans font-medium whitespace-nowrap transition-colors duration-[var(--dur-fast)] focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-acc disabled:cursor-default disabled:opacity-45 ${KIND[kind]} ${SIZE[size]}${iconOnly ? ` ${SQUARE[size]}` : ''}${className ? ` ${className}` : ''}`}
    >
      {Glyph ? <Glyph width={px} height={px} strokeWidth={1.75} aria-hidden /> : null}
      {iconOnly ? null : (children ?? label)}
    </button>
  );
}
