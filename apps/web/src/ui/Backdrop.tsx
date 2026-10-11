// The dimmed, blurred page behind a dialog or the palette, with its content hanging 12vh from the top.
import type { ComponentProps } from 'react';

export function Backdrop({ className, ...rest }: ComponentProps<'div'>) {
  return (
    <div
      {...rest}
      className={`ui-backdrop fixed inset-0 z-40 flex items-start justify-center bg-[var(--scrim)] pt-[12vh] backdrop-blur-[3px]${className ? ` ${className}` : ''}`}
    />
  );
}
