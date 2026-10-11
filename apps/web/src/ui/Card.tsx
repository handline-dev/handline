// Cards: `box` is the bordered surface of a page or a chart; `chat` is the one-line card of the chat stream and the prompt; plus their parts.
import type { ComponentProps, ElementType } from 'react';

const join = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');
const BOX = 'rounded-card border border-line bg-surface';
const CHAT =
  'card my-2 flex items-center gap-2 rounded-card border border-transparent bg-surface px-2.5 py-1.5 text-[13px] shadow-[var(--shadow-card)] [&_svg]:align-middle [&_a]:text-inherit';

/** `open` is told by a thicker frame (an open prompt), not by colour. */
export function Card({
  variant = 'box',
  as: Tag = 'div',
  className,
  ...rest
}: ComponentProps<'div'> & { variant?: 'box' | 'chat'; as?: ElementType }) {
  return (
    <Tag
      {...rest}
      className={join(
        variant === 'box' ? BOX : CHAT,
        variant === 'chat' && 'data-[open]:border-2 data-[open]:border-line-strong',
        className,
      )}
    />
  );
}

/** A chart card: the box with room, headed by a ChartTitle. `chart` is the hook the tests query. */
export function ChartCard({ className, ...rest }: ComponentProps<'section'>) {
  return <section {...rest} className={join('chart', BOX, 'px-4 py-3', className)} />;
}

export function ChartTitle({ className, ...rest }: ComponentProps<'h3'>) {
  return <h3 {...rest} className={join('m-0 mb-2 text-title font-semibold', className)} />;
}

/** A chat card's buttons; `wrap` is a card of the HR page, where they wrap and the card's own gap spaces them. */
export function CardActions({ as: Tag = 'div', wrap, className, ...rest }: ComponentProps<'div'> & { as?: ElementType; wrap?: boolean }) {
  return <Tag {...rest} className={join('card-actions flex gap-2 max-sm:flex-col-reverse', wrap ? 'm-0 flex-wrap' : 'mt-2', className)} />;
}

/** The one-line body of a chat card: it takes the room the icons leave and cuts with an ellipsis; `stacked` puts a title over its reason. */
export function CardWhat({ stacked, className, ...rest }: ComponentProps<'span'> & { stacked?: boolean }) {
  const dir = stacked ? 'flex-col items-start' : 'items-center gap-1.5';
  return (
    <span {...rest} className={join('card-what flex min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap', dir, className)} />
  );
}

export function CardCode({ className, ...rest }: ComponentProps<'code'>) {
  return <code {...rest} className={join('card-code overflow-hidden text-ellipsis font-mono', className)} />;
}

/** The terminal-dark block of a few lines (`lines`) or of a command (`cmd`). */
export function CodeBlock({ cmd, className, ...rest }: ComponentProps<'pre'> & { cmd?: boolean }) {
  const look = 'm-0 block rounded-ctl bg-term-bg px-2 py-1.5 font-mono text-[13px] whitespace-pre-wrap text-term-ink';
  const cls = join(cmd ? 'cmd' : 'lines', look, className);
  return cmd ? <code {...rest} className={cls} /> : <pre {...rest} className={cls} />;
}
