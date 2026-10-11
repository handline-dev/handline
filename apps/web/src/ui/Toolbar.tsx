// A toolbar (Base UI): a wrapping row of actions with a label; the hook `tbar` carries the phone rules.
import { Toolbar as Base } from '@base-ui/react/toolbar';
import type { ComponentProps } from 'react';

export function Toolbar({ label, className, ...rest }: Omit<ComponentProps<'div'>, 'aria-label'> & { label: string }) {
  return (
    <Base.Root {...rest} aria-label={label} className={`tbar flex flex-wrap items-center gap-2 pt-2${className ? ` ${className}` : ''}`} />
  );
}
