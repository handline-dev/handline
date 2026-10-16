// The one menu (Radix Menu): focus, arrow keys, type-ahead, Escape, a click outside and keeping inside the window come from the library;
// the look is ours. `MenuButton` opens under its button, `Menu` + `MenuTrigger` at the cursor on a right-click. A press outside closes it and still reaches what is under it, so a second trigger opens its own menu.
import { cloneElement, createContext, useContext, useRef, useState, type ComponentProps, type MouseEvent, type MutableRefObject, type ReactElement, type ReactNode } from 'react';
import * as D from '@radix-ui/react-menu';
import { Button } from './Button.tsx';
import { Icon, type IconName } from './Icon.tsx';

const PANEL = 'z-30 max-h-[var(--radix-popper-available-height)] overflow-y-auto min-w-[200px] rounded-card border border-line bg-panel py-1 shadow-[var(--shadow-pop)] outline-none';
const ITEM =
  'flex h-8 w-full cursor-default select-none items-center gap-2 px-2 text-left text-body text-ink outline-none data-[highlighted]:bg-raised data-[disabled]:opacity-55 max-sm:min-h-12';

/** Where a menu opens: a point (a right-click) or a box (a button), which the menu keeps clear of when it flips. */
export type At = { x: number; y: number; w?: number; h?: number };
const AtCursor = createContext<((p: At) => void) | null>(null);

/** A menu that opens where a right-click (or `MenuTrigger`'s own `onContextMenu`) lands. */
export function Menu({
  children,
  onOpenChange,
  openRef,
}: {
  children: ReactNode;
  onOpenChange?: (open: boolean) => void;
  /** Filled with a function that opens the menu at a point or box: for a button that is not inside the trigger. */
  openRef?: MutableRefObject<((at: At) => void) | null>;
}) {
  const [open, setOpen] = useState(false);
  const at = useRef<At>({ x: 0, y: 0 });
  const anchor = useRef({ getBoundingClientRect: () => DOMRect.fromRect({ x: at.current.x, y: at.current.y, width: at.current.w ?? 0, height: at.current.h ?? 0 }) });
  const change = (o: boolean) => (setOpen(o), onOpenChange?.(o));
  const openAt = (p: At) => ((at.current = p), change(true));
  if (openRef) openRef.current = openAt;
  return (
    <AtCursor.Provider value={openAt}>
      <D.Root open={open} onOpenChange={change} modal={false}>
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
      <D.Content className={PANEL} align="start" collisionPadding={8} onCloseAutoFocus={(e) => e.preventDefault()} {...rest}>
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
  trigger,
  ...button
}: { children: ReactNode; trigger?: ReactNode; name?: string; align?: 'start' | 'end'; open?: boolean; onOpenChange?: (open: boolean) => void } & Omit<
  ComponentProps<typeof Button>,
  'onClick' | 'aria-haspopup' | 'aria-expanded'
>) {
  const [own, setOwn] = useState(false);
  const open = shown ?? own;
  const setOpen = (o: boolean) => (setOwn(o), onOpenChange?.(o));
  // A press on this button while its menu is open closes the menu first; the click that follows must not open it again.
  const wasOpen = useRef(false);
  return (
    <D.Root open={open} onOpenChange={setOpen} modal={false}>
      <D.Anchor asChild>
        <span className="inline-flex">
          <Button
            {...button}
            aria-haspopup="menu"
            aria-expanded={open}
            onPointerDownCapture={() => (wasOpen.current = open)}
            onClick={() => {
              if (!wasOpen.current) setOpen(!open);
              wasOpen.current = false;
            }}
          >
            {trigger}
          </Button>
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
