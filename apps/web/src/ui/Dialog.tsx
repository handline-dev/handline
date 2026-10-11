// A dialog (§1.6): the page dimmed and blurred behind it, 460 px wide, a title, the body, and one solid button at the bottom right with its
// shortcut. Escape closes it; focus goes into it once when it opens and back to where it was when it closes. An action with `form` submits
// that form (so Enter in a field sends too); one with `onClick` just calls it.
import { useEffect, useRef, type ReactNode } from 'react';
import { Button } from './Button.tsx';

export function Dialog({
  title,
  onClose,
  action,
  children,
}: {
  title: string;
  onClose: () => void;
  action?: { label: string; keys?: string; onClick?: () => void; form?: string; disabled?: boolean };
  children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const live = useRef({ onClose, action });
  live.current = { onClose, action };
  useEffect(() => {
    const was = document.activeElement as HTMLElement | null;
    box.current?.querySelector<HTMLElement>('input, textarea, select, button')?.focus();
    const key = (e: KeyboardEvent) => {
      const { onClose, action } = live.current;
      if (e.key === 'Escape') onClose();
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && action && !action.disabled) {
        e.preventDefault(); // no implicit submit on top of this one
        if (action.form) document.forms.namedItem(action.form)?.requestSubmit();
        else action.onClick?.();
      }
    };
    addEventListener('keydown', key);
    return () => {
      removeEventListener('keydown', key);
      was?.focus?.();
    };
  }, []);
  return (
    <div className="ui-backdrop" onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="ui-dialog flex w-[min(var(--w-dialog),calc(100vw_-_2_*_var(--sp-6)))] flex-col gap-3 rounded-dialog border border-line-strong bg-panel p-4 shadow-[var(--shadow-pop)]"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={box}
      >
        <h2 className="m-0 text-title font-semibold">{title}</h2>
        <div className="ui-dialog-body flex flex-col gap-3">{children}</div>
        {action ? (
          <footer className="flex justify-end">
            <Button
              variant="solid"
              label={action.label}
              keys={action.keys ?? '⌘↵'}
              disabled={action.disabled}
              type={action.form ? 'submit' : 'button'}
              form={action.form}
              onClick={action.onClick}
            />
          </footer>
        ) : null}
      </div>
    </div>
  );
}
