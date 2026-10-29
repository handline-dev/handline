// The one menu (Radix Menu): focus, arrow keys, type-ahead, Escape, a click outside and keeping inside the window come from the library;
// the look is ours. `MenuButton` opens under its button, `Menu` + `MenuTrigger` at the cursor on a right-click. A press outside closes it and still reaches what is under it, so a second trigger opens its own menu.
import {
  cloneElement,
  createContext,
  useContext,
  useRef,
  useState,
  type ComponentProps,
  type MouseEvent,
  type MutableRefObject,
  type ReactElement,
  type ReactNode,
} from 'react';
import * as D from '@radix-ui/react-menu';
import { motion } from 'framer-motion';
import { AnimatedList } from '#src/AnimatedList.tsx';
import { Button } from '#src/ui/Button.tsx';
import { Icon, type IconName } from '#src/ui/Icon.tsx';
import { GROW, useOpenMotion } from '#src/ui/Reveal.tsx';

const PANEL =
  'z-30 overflow-y-auto min-w-[200px] rounded-md border border-line bg-panel py-[calc(4px*var(--o,1))] shadow-[var(--shadow)] outline-none';
const ITEM =
  'flex h-8 w-full cursor-default select-none items-center gap-2 px-2 text-left text-md text-ink outline-none data-[highlighted]:bg-raised data-[disabled]:opacity-55 max-sm:min-h-12';

/** Where a menu opens: a point (a right-click) or a box (a button), which the menu keeps clear of when it flips. */
export type At = { x: number; y: number; w?: number; h?: number };
// The room a menu may take: the larger side of its anchor. Radix's own available-height shrinks the panel to the side it first picked,
// and a panel that has shrunk to fit never flips; this keeps it whole until neither side holds it.
const PAD = 8;
const roomAround = (a: { top: number; bottom: number }) => Math.max(a.top, window.innerHeight - a.bottom) - PAD - 4;
const AnchorBox = createContext<(() => DOMRect | null) | null>(null);
/** Whether the menu around is open: its content stays mounted (`forceMount`) until it has closed. */
const Opened = createContext(false);
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
  const anchor = useRef({
    getBoundingClientRect: () =>
      DOMRect.fromRect({ x: at.current.x, y: at.current.y, width: at.current.w ?? 0, height: at.current.h ?? 0 }),
  });
  const change = (o: boolean) => (setOpen(o), onOpenChange?.(o));
  const openAt = (p: At) => ((at.current = p), change(true));
  if (openRef) openRef.current = openAt;
  return (
    <AtCursor.Provider value={openAt}>
      <AnchorBox.Provider value={() => anchor.current.getBoundingClientRect()}>
        <D.Root open={open} onOpenChange={change} modal={false}>
          <Opened.Provider value={open}>
            <D.Anchor virtualRef={anchor} />
            {children}
          </Opened.Provider>
        </D.Root>
      </AnchorBox.Provider>
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

export function MenuContent({ children, style, ...rest }: ComponentProps<typeof D.Content>) {
  const box = useContext(AnchorBox)?.();
  const open = useContext(Opened);
  const grow = useOpenMotion(GROW);
  const maxHeight = box ? roomAround(box) : undefined;
  return (
    <AnimatedList>
      {open ? (
        <D.Portal forceMount>
          <D.Content asChild forceMount align="start" collisionPadding={PAD} onCloseAutoFocus={(e) => e.preventDefault()} {...rest}>
            <motion.div {...grow} className={PANEL} style={{ maxHeight, ...style }}>
              {children}
            </motion.div>
          </D.Content>
        </D.Portal>
      ) : null}
    </AnimatedList>
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
}: {
  children: ReactNode;
  trigger?: ReactNode;
  name?: string;
  align?: 'start' | 'end';
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
} & Omit<ComponentProps<typeof Button>, 'onClick' | 'aria-haspopup' | 'aria-expanded'>) {
  const [own, setOwn] = useState(false);
  const open = shown ?? own;
  const setOpen = (o: boolean) => (setOwn(o), onOpenChange?.(o));
  // A press on this button while its menu is open closes the menu first; the click that follows must not open it again.
  const wasOpen = useRef(false);
  const anchor = useRef<HTMLSpanElement>(null);
  return (
    <AnchorBox.Provider value={() => anchor.current?.getBoundingClientRect() ?? null}>
      <D.Root open={open} onOpenChange={setOpen} modal={false}>
        <Opened.Provider value={open}>
          <D.Anchor asChild>
            <span ref={anchor} className="inline-flex">
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
            {name ? <D.Label className="px-2 py-1 text-xs text-ink-3">{name}</D.Label> : null}
            {children}
          </MenuContent>
        </Opened.Provider>
      </D.Root>
    </AnchorBox.Provider>
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

export function MenuItem({
  icon,
  danger,
  children,
  ...rest
}: { icon?: IconName; danger?: boolean; children: ReactNode } & ComponentProps<typeof D.Item>) {
  return (
    <D.Item className={danger ? `${ITEM} text-bad` : ITEM} {...rest}>
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

export const MenuLabel = ({ children }: { children: ReactNode }) => <D.Label className="px-2 py-1 text-xs text-ink-3">{children}</D.Label>;

export const MenuSeparator = () => <D.Separator className="my-1 h-px bg-line" />;
