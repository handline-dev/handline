// The button: three kinds — solid (the one main action of a view), outline (the quiet filled one, no border) and ghost (text only); icon-only is
// a square with its label as the tooltip. `keys` is the shortcut printed on a solid button.
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Icon, type IconName } from '#src/ui/Icon.tsx';

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant?: 'ghost' | 'outline' | 'solid';
  size?: 'sm' | 'md' | 'lg';
  icon?: IconName;
  /** A write is in flight: the icon is the spinning working one and the button is disabled. */
  busy?: boolean;
  /** The tooltip and aria-label; also the words of the button when `children` is absent and there is no icon. */
  label: string;
  keys?: string;
  children?: ReactNode;
}

const H = { sm: 'h-7', md: 'h-8', lg: 'h-9' } as const;
const PAD = { sm: 'px-2', md: 'px-3', lg: 'px-4' } as const;

export function Button({
  variant = 'ghost',
  size = 'md',
  icon,
  busy,
  label,
  keys,
  children,
  className,
  type = 'button',
  ...rest
}: ButtonProps) {
  const iconOnly = !!icon && children == null;
  const fit = iconOnly ? (size === 'sm' ? 'w-7 p-0' : size === 'md' ? 'w-8 p-0' : 'w-9 p-0') : PAD[size];
  const look =
    variant === 'solid'
      ? 'bg-primary font-medium text-on-primary hover:enabled:brightness-110'
      : variant === 'outline'
        ? 'bg-panel font-medium hover:enabled:bg-raised'
        : 'bg-transparent font-normal hover:enabled:bg-raised';
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      type={type}
      className={`ui-btn ${variant === 'outline' ? 'quiet' : variant} ${size}${iconOnly ? ' icon-only' : ''} inline-flex items-center justify-center gap-1.5 ${H[size]} ${fit} rounded-ctl border border-transparent font-sans text-body text-ink whitespace-nowrap ${look}${className ? ` ${className}` : ''}`}
      title={rest.title ?? label}
      aria-label={rest['aria-label'] ?? (iconOnly ? label : undefined)}
    >
      {icon ? (
        <Icon
          name={busy ? 'working' : icon}
          label={label}
          size={size === 'sm' ? 14 : 16}
          aria-hidden={iconOnly ? undefined : true}
          className={busy ? 'animate-[ui-spin_1.2s_linear_infinite] motion-reduce:animate-none' : undefined}
        />
      ) : null}
      {children ?? (icon ? null : label)}
      {keys ? <kbd className="ui-keys ml-1 rounded-[3px] border border-current/30 px-1 font-sans text-hint opacity-70">{keys}</kbd> : null}
    </button>
  );
}
