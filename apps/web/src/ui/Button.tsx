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

export function Button({ variant = 'ghost', size = 'md', icon, label, keys, children, className, type = 'button', ...rest }: ButtonProps) {
  const iconOnly = !!icon && children == null;
  return (
    <button
      {...rest}
      type={type}
      className={`ui-btn ${variant} ${size}${iconOnly ? ' icon-only' : ''}${className ? ` ${className}` : ''}`}
      title={rest.title ?? label}
      aria-label={iconOnly ? label : undefined}
    >
      {icon ? <Icon name={icon} label={label} size={size === 'sm' ? 14 : 16} /> : null}
      {children ?? (icon ? null : label)}
      {keys ? <kbd className="ui-keys">{keys}</kbd> : null}
    </button>
  );
}
