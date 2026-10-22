// A popup under its trigger (§1.6): a header with the title and an optional refresh, rows of 28 px, closed by Escape or a click outside.
// `PopupRow` is a row; `opens` adds the chevron of a row that opens a second popup.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { placePopover, type Placement } from '@handline/app-ui';
import { motion } from 'framer-motion';
import { AnimatedList } from '#src/AnimatedList.tsx';
import { Button } from '#src/ui/Button.tsx';
import { Icon, type IconName } from '#src/ui/Icon.tsx';
import { useOpenMotion } from '#src/ui/Reveal.tsx';

export function Popup({
  open,
  onClose,
  title,
  onRefresh,
  children,
  align = 'left',
  up,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  onRefresh?: () => void;
  children: ReactNode;
  align?: 'left' | 'right';
  /** Where it opens first; it flips to the other side, or edge, when the window leaves no room there. */
  up?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    const away = (e: PointerEvent) => !box.current?.parentElement?.contains(e.target as Node) && onClose();
    addEventListener('keydown', key);
    addEventListener('pointerdown', away);
    return () => {
      removeEventListener('keydown', key);
      removeEventListener('pointerdown', away);
    };
  }, [open, onClose]);
  const grow = useOpenMotion();
  const [placed, setPlaced] = useState<(Placement & { tight: boolean }) | null>(null);
  useLayoutEffect(() => {
    const el = box.current;
    const anchor = el?.parentElement?.getBoundingClientRect();
    if (!open || !el || !anchor) return setPlaced(null);
    const w = Math.max(el.offsetWidth, 220);
    const at = placePopover(
      anchor,
      { w, h: el.scrollHeight },
      { w: innerWidth, h: innerHeight },
      { side: up ? 'top' : 'bottom', align: align === 'right' ? 'end' : 'start' },
    );
    setPlaced({ ...at, tight: at.maxHeight < el.scrollHeight });
  }, [open, up, align]);
  const top = placed ? placed.side === 'top' : !!up;
  const end = placed ? placed.align === 'end' : align === 'right';
  return (
    <AnimatedList>
      {open ? (
        <motion.div
          {...grow}
          className={`ui-popup ${end ? 'right' : 'left'}${top ? ' up' : ''} absolute z-20 mt-1 min-w-[220px] rounded-card bg-panel pb-[calc(4px*var(--o,1))] shadow-[var(--shadow-2)]${end ? ' right-0' : ''}${top ? ' bottom-full mb-1' : ''}`}
          role="dialog"
          aria-label={title}
          ref={box}
          style={{ overflowY: placed?.tight ? 'auto' : 'clip', maxHeight: placed?.tight ? placed.maxHeight : undefined }}
        >
          <header className="flex items-center justify-between border-b border-line px-2 py-1.5 font-sans text-body font-medium">
            <strong>{title}</strong>
            {onRefresh ? <Button icon="refresh" label="Refresh" size="sm" onClick={onRefresh} /> : null}
          </header>
          {children}
        </motion.div>
      ) : null}
    </AnimatedList>
  );
}

/** One row of a popup or menu; Sidebar's ⋯ rows and the palette's list ride the same look. */
export const PROW = 'ui-prow flex h-7 w-full items-center gap-2 border-0 bg-transparent px-2 text-left text-ink hover:bg-raised';
export const PROW_NAME = 'ui-prow-name flex-1';
export const PROW_HINT = 'ui-prow-hint text-hint text-ink-3';

export function PopupRow({
  icon,
  label,
  hint,
  opens,
  onClick,
}: {
  icon?: IconName;
  label: string;
  hint?: string;
  opens?: boolean;
  onClick?: () => void;
}) {
  return (
    <button type="button" className={`${PROW} max-sm:min-h-12`} onClick={onClick}>
      {icon ? <Icon name={icon} size={14} label={label} aria-hidden /> : null}
      <span className={PROW_NAME}>{label}</span>
      {hint ? <span className={PROW_HINT}>{hint}</span> : null}
      {opens ? <Icon name="chevronRight" size={14} label="Opens a list" aria-hidden /> : null}
    </button>
  );
}
