// The bordered text controls outside a Field: Input and Textarea on the canvas, NativeSelect and the compact bar input on the panel.
import type { ComponentProps } from 'react';

const join = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');
const FIELD = 'rounded-ctl border border-line bg-canvas text-ink';
const BAR = 'h-[28px] rounded-ctl border border-line bg-panel px-1.5 text-body text-ink';

/** `grow` takes the free width of its row (min 10 rem); `dense` is the tighter padding of an inline answer. */
type Fit = { grow?: boolean; dense?: boolean };
const fit = ({ grow, dense }: Fit) => join(dense ? 'px-2 py-1' : 'px-2 py-1.5', grow && 'min-w-40 flex-1');

export function Input({ grow, dense, className, ...rest }: ComponentProps<'input'> & Fit) {
  return <input {...rest} className={join(FIELD, fit({ grow, dense }), className)} />;
}

export function Textarea({ grow, dense, className, ...rest }: ComponentProps<'textarea'> & Fit) {
  return <textarea {...rest} className={join(FIELD, fit({ grow, dense }), className)} />;
}

/** The 28 px search box of a toolbar. */
export function BarInput({ className, ...rest }: ComponentProps<'input'>) {
  return <input {...rest} className={join(BAR, className)} />;
}

/** The browser's own select, 28 px, for a sheet or a toolbar. */
export function NativeSelect({ className, ...rest }: ComponentProps<'select'>) {
  return <select {...rest} className={join(BAR, className)} />;
}

/** The row under a chat: the message box and its send button. */
export function ComposeRow({ className, ...rest }: ComponentProps<'div'>) {
  return <div {...rest} className={join('compose-row relative flex items-end gap-1.5 border-t border-line px-3 py-2.5', className)} />;
}
