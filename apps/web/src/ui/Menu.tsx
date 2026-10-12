// The context menu (Radix): focus, arrow keys, type-ahead and Escape come from the library; the look is ours (the popup's tokens).
import type { ComponentProps, ReactNode } from 'react';
import * as R from '@radix-ui/react-context-menu';
import { Icon, type IconName } from './Icon.tsx';

export const Menu = R.Root;
export const MenuTrigger = R.Trigger;

const PANEL = 'z-30 min-w-[200px] rounded-card border border-line bg-panel py-1 shadow-[var(--shadow-pop)] outline-none';
const ITEM =
  'flex h-8 w-full cursor-default select-none items-center gap-2 px-2 text-left text-body text-ink outline-none data-[highlighted]:bg-raised data-[disabled]:opacity-55 max-sm:min-h-12';

export function MenuContent({ children, ...rest }: ComponentProps<typeof R.Content>) {
  return (
    <R.Portal>
      <R.Content className={PANEL} {...rest}>
        {children}
      </R.Content>
    </R.Portal>
  );
}

export function MenuItem({
  icon,
  danger,
  children,
  ...rest
}: { icon?: IconName; danger?: boolean; children: ReactNode } & ComponentProps<typeof R.Item>) {
  return (
    <R.Item className={`${ITEM}${danger ? ' text-bad' : ''}`} {...rest}>
      <span className="flex w-4 flex-none justify-center">{icon ? <Icon name={icon} size={14} aria-hidden /> : null}</span>
      <span className="flex-1">{children}</span>
    </R.Item>
  );
}

export const MenuSub = R.Sub;

export function MenuSubTrigger({ icon, children }: { icon?: IconName; children: ReactNode }) {
  return (
    <R.SubTrigger className={ITEM}>
      <span className="flex w-4 flex-none justify-center">{icon ? <Icon name={icon} size={14} aria-hidden /> : null}</span>
      <span className="flex-1">{children}</span>
      <Icon name="chevronRight" size={12} aria-hidden />
    </R.SubTrigger>
  );
}

export function MenuSubContent({ children }: { children: ReactNode }) {
  return (
    <R.Portal>
      <R.SubContent className={PANEL} sideOffset={2}>
        {children}
      </R.SubContent>
    </R.Portal>
  );
}

export const MenuSeparator = () => <R.Separator className="my-1 h-px bg-line" />;
