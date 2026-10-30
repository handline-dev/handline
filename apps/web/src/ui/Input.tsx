// The bordered text controls outside a Field: Input and Textarea on the canvas, NativeSelect and the compact bar input on the panel.
import type { ComponentProps } from 'react';

const join = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');
const FIELD = 'rounded-sm border border-line bg-panel text-ink';
const BAR = 'h-8 rounded-sm border border-line bg-panel px-2 text-md text-ink';

/** `grow` takes the free width of its row (min 10 rem); `dense` is the tighter padding of an inline answer. */
type Fit = { grow?: boolean; dense?: boolean };
const fit = ({ grow, dense }: Fit) => join(dense ? 'px-2.5 py-1.5' : 'px-3 py-2', grow && 'min-w-40 flex-1');

export function Input({ grow, dense, className, ...rest }: ComponentProps<'input'> & Fit) {
  return <input {...rest} className={join(FIELD, fit({ grow, dense }), className)} />;
}

export function Textarea({ grow, dense, className, ...rest }: ComponentProps<'textarea'> & Fit) {
  return <textarea {...rest} className={join(FIELD, fit({ grow, dense }), className)} />;
}

/** The 32 px search box of a toolbar. */
export function BarInput({ className, ...rest }: ComponentProps<'input'>) {
  return <input {...rest} className={join(BAR, className)} />;
}

/** The browser's own select, 32 px, for a sheet or a toolbar. */
export function NativeSelect({ className, ...rest }: ComponentProps<'select'>) {
  return <select {...rest} className={join(BAR, className)} />;
}

/** The composer under a chat: a rounded surface with the message box over a bar of tools and the send button — tone on tone, no border, no shadow. */
export function ComposeRow({ className, ...rest }: ComponentProps<'div'>) {
  return <div {...rest} className={join('compose-row relative mx-3 mb-3 max-w-[680px] rounded-lg bg-surface px-4 py-3', className)} />;
}
