// The one menu (Radix Menu): focus, arrow keys, type-ahead, Escape, a click outside and keeping inside the window come from the library;
// the look is ours. `MenuButton` opens under its button, `Menu` + `MenuTrigger` at the cursor on a right-click. A menu is modal, so only one is open.
import { cloneElement, createContext, useContext, useRef, useState, type ComponentProps, type MouseEvent, type ReactElement, type ReactNode } from 'react';
import * as D from '@radix-ui/react-menu';
import { Button } from './Button.tsx';
import { Icon, type IconName } from './Icon.tsx';

const PANEL = 'z-30 min-w-[200px] rounded-card border border-line bg-panel py-1 shadow-[var(--shadow-pop)] outline-none';
const ITEM =
  'flex h-8 w-full cursor-default select-none items-center gap-2 px-2 text-left text-body text-ink outline-none data-[highlighted]:bg-raised data-[disabled]:opacity-55 max-sm:min-h-12';

type Point = { x: number; y: number };
const AtCursor = createContext<((p: Point) => void) | null>(null);

/** A menu that opens where a right-click (or `MenuTrigger`'s own `onContextMenu`) lands. */
export function Menu({ children, onOpenChange }: { children: ReactNode; onOpenChange?: (open: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const at = useRef<Point>({ x: 0, y: 0 });
  const anchor = useRef({ getBoundingClientRect: () => DOMRect.fromRect({ x: at.current.x, y: at.current.y, width: 0, height: 0 }) });
  const change = (o: boolean) => (setOpen(o), onOpenChange?.(o));
  return (
    <AtCursor.Provider value={(p) => ((at.current = p), change(true))}>
      <D.Root open={open} onOpenChange={change} modal>
        <D.Anchor virtualRef={anchor} />
        {children}
      </D.Root>
    </AtCursor.Provider>
  );
}

/** Makes its one child element open the menu on a right-click. */
export function MenuTrigger({ children }: { asChild?: boolean; children: ReactElement<{ onContextMenu?: (e: MouseEvent) => void }> }) {
  const open = useContext(AtCursor);
  return cloneElement(children, {
    onContextMenu: (e: MouseEvent) => {
      children.props.onContextMenu?.(e);
      e.preventDefault();
      open?.({ x: e.clientX, y: e.clientY });
    },
  });
}

export function MenuContent({ children, ...rest }: ComponentProps<typeof D.Content>) {
  return (
    <D.Portal>
      <D.Content className={PANEL} collisionPadding={8} onCloseAutoFocus={(e) => e.preventDefault()} {...rest}>
        {children}
      </D.Content>
    </D.Portal>
  );
}

/** A button that opens `children` (MenuItem, MenuSeparator) under it, flipped to stay in the window. `name` is the menu's own title. */
export function MenuButton({
  children,
  name,
  align = 'end',
  open: shown,
  onOpenChange,
  ...button
}: { children: ReactNode; name?: string; align?: 'start' | 'end'; open?: boolean; onOpenChange?: (open: boolean) => void } & Omit<
  ComponentProps<typeof Button>,
  'onClick' | 'aria-haspopup' | 'aria-expanded'
>) {
  const [own, setOwn] = useState(false);
  const open = shown ?? own;
  const setOpen = (o: boolean) => (setOwn(o), onOpenChange?.(o));
  return (
    <D.Root open={open} onOpenChange={setOpen} modal>
      <D.Anchor asChild>
        <span className="inline-flex">
          <Button {...button} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)} />
        </span>
      </D.Anchor>
      <MenuContent side="bottom" align={align} sideOffset={4} aria-label={name}>
        {name ? <D.Label className="px-2 py-1 text-hint text-ink-3">{name}</D.Label> : null}
        {children}
      </MenuContent>
    </D.Root>
  );
}

const Lead = ({ icon }: { icon?: IconName }) => (
  <span className="flex w-4 flex-none justify-center">{icon ? <Icon name={icon} size={14} aria-hidden /> : null}</span>
);

/** A row that is on or off (the filter's). */
export function MenuCheckItem({
  checked,
  onCheckedChange,
  children,
  icon,
}: {
  checked: boolean;
  onCheckedChange: (on: boolean) => void;
  children: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <D.CheckboxItem className={ITEM} checked={checked} onCheckedChange={onCheckedChange} onSelect={(e) => e.preventDefault()}>
      <span className="flex w-4 flex-none justify-center">{checked ? <Icon name="markRead" size={12} aria-hidden /> : null}</span>
      {icon}
      <span className="flex flex-1 items-center gap-2">{children}</span>
    </D.CheckboxItem>
  );
}

export function MenuItem({ icon, children, ...rest }: { icon?: IconName; children: ReactNode } & ComponentProps<typeof D.Item>) {
  return (
    <D.Item className={ITEM} {...rest}>
      <Lead icon={icon} />
      <span className="flex flex-1 items-center">{children}</span>
    </D.Item>
  );
}

export const MenuSub = D.Sub;

export function MenuSubTrigger({ icon, children }: { icon?: IconName; children: ReactNode }) {
  return (
    <D.SubTrigger className={ITEM}>
      <Lead icon={icon} />
      <span className="flex-1">{children}</span>
      <Icon name="chevronRight" size={12} aria-hidden />
    </D.SubTrigger>
  );
}

export function MenuSubContent({ children }: { children: ReactNode }) {
  return (
    <D.Portal>
      <D.SubContent className={PANEL} sideOffset={2}>
        {children}
      </D.SubContent>
    </D.Portal>
  );
}

export const MenuSeparator = () => <D.Separator className="my-1 h-px bg-line" />;
