// The button (§1.6): ghost by default, outline in a group, solid for the one main action of a view; icon-only is a 28 px square with its label
// as the tooltip. `keys` is the shortcut printed on a solid button.
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Icon, type IconName } from './Icon.tsx';

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant?: 'ghost' | 'outline' | 'solid';
  size?: 'sm' | 'md' | 'lg';
  icon?: IconName;
  /** The tooltip and aria-label; also the words of the button when `children` is absent and there is no icon. */
  label: string;
  keys?: string;
  children?: ReactNode;
}

const H = { sm: 'h-6', md: 'h-7', lg: 'h-8' } as const;
const PAD = { sm: 'px-1.5', md: 'px-2', lg: 'px-3' } as const;

export function Button({ variant = 'ghost', size = 'md', icon, label, keys, children, className, type = 'button', ...rest }: ButtonProps) {
  const iconOnly = !!icon && children == null;
  const fit = iconOnly ? (size === 'sm' ? 'w-6 p-0' : 'w-7 p-0') : PAD[size];
  const look =
    variant === 'solid'
      ? 'bg-primary font-medium text-on-primary hover:enabled:opacity-88'
      : 'bg-transparent font-normal hover:enabled:bg-raised';
  return (
    <button
      {...rest}
      type={type}
      className={`ui-btn ${variant} ${size}${iconOnly ? ' icon-only' : ''} inline-flex items-center justify-center gap-1.5 ${H[size]} ${fit} rounded-ctl border ${
        variant === 'outline' ? 'border-line-strong' : 'border-transparent'
      } font-sans text-body text-ink whitespace-nowrap ${look}${className ? ` ${className}` : ''}`}
      title={rest.title ?? label}
      aria-label={rest['aria-label'] ?? (iconOnly ? label : undefined)}
    >
      {icon ? <Icon name={icon} label={label} size={size === 'sm' ? 14 : 16} aria-hidden={iconOnly ? undefined : true} /> : null}
      {children ?? (icon ? null : label)}
      {keys ? <kbd className="ui-keys ml-1 rounded-[3px] border border-line px-1 font-sans text-hint opacity-70">{keys}</kbd> : null}
    </button>
  );
}
