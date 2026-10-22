// A checkbox: the browser's own input, drawn in the tokens (filled with the primary colour and a tick when on).
import type { ComponentProps } from 'react';

export function Check({ className, ...rest }: Omit<ComponentProps<'input'>, 'type'>) {
  return (
    <input
      {...rest}
      type="checkbox"
      className={`ui-check m-0 h-[18px] w-[18px] shrink-0 cursor-pointer appearance-none rounded-chip border border-line-strong bg-panel checked:border-primary checked:bg-primary disabled:opacity-50${className ? ` ${className}` : ''}`}
    />
  );
}
