// A popup under its trigger (§1.6): a header with the title and an optional refresh, rows of 28 px, closed by Escape or a click outside.
// `PopupRow` is a row; `opens` adds the chevron of a row that opens a second popup.
import { useEffect, useRef, type ReactNode } from 'react';
import { Button } from './Button.tsx';
import { Icon, type IconName } from './Icon.tsx';

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
  /** Opens above its trigger (a trigger at the bottom of a pane). */
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
  if (!open) return null;
  return (
    <div
      className={`ui-popup ${align}${up ? ' up' : ''} absolute z-20 mt-1 min-w-[220px] rounded-card border border-line-strong bg-panel pb-1 shadow-[var(--shadow-pop)]${align === 'right' ? ' right-0' : ''}${up ? ' bottom-full mb-1' : ''}`}
      role="dialog"
      aria-label={title}
      ref={box}
    >
      <header className="flex items-center justify-between border-b border-line px-2 py-1.5 font-sans text-body font-medium">
        <strong>{title}</strong>
        {onRefresh ? <Button icon="refresh" label="Refresh" size="sm" onClick={onRefresh} /> : null}
      </header>
      {children}
    </div>
  );
}

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
    <button type="button" className="ui-prow max-sm:min-h-12" onClick={onClick}>
      {icon ? <Icon name={icon} size={14} label={label} aria-hidden /> : null}
      <span className="ui-prow-name">{label}</span>
      {hint ? <span className="ui-prow-hint">{hint}</span> : null}
      {opens ? <Icon name="chevronRight" size={14} label="Opens a list" aria-hidden /> : null}
    </button>
  );
}
