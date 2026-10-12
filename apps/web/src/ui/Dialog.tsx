// A dialog (§1.6, Base UI): the page dimmed and blurred behind it, 460 px wide, a title, the body, and one solid button at the bottom right with its
// shortcut. Escape closes it; focus goes into it once when it opens and back to where it was when it closes. An action with `form` submits
// that form (so Enter in a field sends too); one with `onClick` just calls it.
import { Dialog as Base } from '@base-ui/react/dialog';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Backdrop } from './Backdrop.tsx';
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
  const [host, setHost] = useState<HTMLElement | null>(null); // the dialog stays where it is mounted, not in <body>
  const box = useRef<HTMLDivElement>(null);
  const live = useRef({ action });
  live.current = { action };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const { action } = live.current;
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && action && !action.disabled) {
        e.preventDefault(); // no implicit submit on top of this one
        if (action.form) document.forms.namedItem(action.form)?.requestSubmit();
        else action.onClick?.();
      }
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  }, []);
  return (
    <>
      <div ref={setHost} />
      <Base.Root open modal onOpenChange={(open) => !open && onClose()}>
        {host ? (
          <Base.Portal container={host}>
            <Backdrop>
              <Base.Popup
                initialFocus={() => box.current?.querySelector<HTMLElement>('input, textarea, select, button') ?? true}
                ref={box}
                className="ui-dialog flex w-[min(var(--w-dialog),calc(100vw_-_2_*_var(--sp-6)))] flex-col gap-3 rounded-dialog bg-panel p-4 shadow-[var(--shadow-pop)]"
                aria-label={title}
                aria-modal="true"
              >
                <Base.Title className="m-0 text-title font-semibold">{title}</Base.Title>
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
              </Base.Popup>
            </Backdrop>
          </Base.Portal>
        ) : null}
      </Base.Root>
    </>
  );
}
