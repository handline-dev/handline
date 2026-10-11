// A key cap: the key printed in a small bordered box.
import type { ComponentProps } from 'react';

export function Kbd({ className, ...rest }: ComponentProps<'kbd'>) {
  return <kbd {...rest} className={`rounded-[3px] border border-line px-1 font-mono text-[11px]${className ? ` ${className}` : ''}`} />;
}
