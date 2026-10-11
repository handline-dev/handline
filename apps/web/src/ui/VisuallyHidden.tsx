// A word only assistive tech reads: clipped to a pixel, still in the accessibility tree.
import type { ComponentProps } from 'react';

export function VisuallyHidden({ className, ...rest }: ComponentProps<'span'>) {
  return (
    <span
      {...rest}
      className={`vh absolute h-px w-px overflow-hidden whitespace-nowrap [clip-path:inset(50%)]${className ? ` ${className}` : ''}`}
    />
  );
}
