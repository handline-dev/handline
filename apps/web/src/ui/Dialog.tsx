// A dialog (§1.6, Base UI): the page dimmed and blurred behind it, 460 px wide, a title, the body, and one solid button at the bottom right with its
// shortcut. Escape closes it; focus goes into it once when it opens and back to where it was when it closes. An action with `form` submits
// that form (so Enter in a field sends too); one with `onClick` just calls it.
import { Dialog as Base } from '@base-ui/react/dialog';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Backdrop } from '#src/ui/Backdrop.tsx';
import { motion } from 'framer-motion';
import { Button } from '#src/ui/Button.tsx';
import { FADE, GROW, useOpenMotion } from '#src/ui/Reveal.tsx';

export function Dialog({
  title,
  onClose,
  action,
  full,
  bar,
  children,
}: {
  title: string;
  onClose: () => void;
  action?: { label: string; keys?: string; onClick?: () => void; form?: string; disabled?: boolean };
  /** Nearly the whole window, for a body that is a picture: the title is only for screen readers. */
  full?: boolean;
  /** In the footer, left of the action. */
  bar?: ReactNode;
  children: ReactNode;
}) {
  const [host, setHost] = useState<HTMLElement | null>(null); // the dialog stays where it is mounted, not in <body>
  const box = useRef<HTMLDivElement>(null);
  const open = useOpenMotion(full ? FADE : GROW); // a full one has a fixed height of its own, so it only fades
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
            <Backdrop style={full ? { paddingTop: 0, alignItems: 'center' } : undefined}>
              <Base.Popup
                initialFocus={() => box.current?.querySelector<HTMLElement>('input, textarea, select, button') ?? true}
                ref={box}
                render={<motion.div {...open} style={{ overflowY: 'clip' }} />}
                className={`ui-dialog flex flex-col gap-3 rounded-lg bg-panel shadow-[var(--shadow)] ${
                  full
                    ? 'h-[calc(100vh_-_var(--sp-2))] w-[calc(100vw_-_var(--sp-2))] p-2'
                    : 'w-[min(var(--w-dialog),calc(100vw_-_2_*_var(--sp-4)))] p-[calc(20px*var(--o,1))]'
                }`}
                aria-label={title}
                aria-modal="true"
              >
                <Base.Title className={full ? 'sr-only' : 'm-0 text-lg font-semibold'}>{title}</Base.Title>
                <div className={`ui-dialog-body flex flex-col gap-3${full ? ' min-h-0 flex-1' : ''}`}>{children}</div>
                {action ? (
                  <footer className="flex items-center justify-end gap-2">
                    {bar}
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
