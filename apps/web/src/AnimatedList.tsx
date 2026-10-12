// The one list primitive: an item added grows from height 0 to auto, one removed shrinks back, one moved slides. Reduced motion skips all of it.
// <AnimatedList> wraps the items; each item is an <AnimatedItem> (any element) or, in a table, an <AnimatedRow> of <AnimatedCell>s.
import type { ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotion, type Variants } from 'framer-motion';

const DURATION = 0.18;
/** The table row height, one CSS variable (`--trow-h`) shared with styles.css, which holds a cell at it as a minimum. */
const rowHeight = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--trow-h')) || 48;
const LAYOUT_MAX = 60; // past this many rows, moves are not animated: measuring every row on each update would thrash

const grow: Variants = {
  hidden: { height: 0, opacity: 0 },
  shown: { height: 'auto', opacity: 1 },
};

/** Under reduced motion nothing is animated: the parts render as the plain elements, and a removed item leaves at once. */
export function AnimatedList({ children }: { children: ReactNode }) {
  return useReducedMotion() ? <>{children}</> : <AnimatePresence initial={false}>{children}</AnimatePresence>;
}

const transition = { duration: DURATION, ease: 'easeOut' as const };

/** One item of a list of blocks. A `key` is required: it is what tells an add from a move.
 *  `layout` slides it when the list reorders (leave it off for a list that only grows at the end); `instant` skips the enter animation
 *  (items that arrive above the reader or all at once); `onFrame` runs on every frame of the height change. */
export function AnimatedItem({
  children,
  className,
  layout = false,
  instant = false,
  onFrame,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  layout?: boolean;
  instant?: boolean;
  onFrame?: () => void;
} & Record<`data-${string}`, string | undefined>) {
  const cls = `anim-item${className ? ` ${className}` : ''}`;
  if (useReducedMotion())
    return (
      <div {...rest} className={cls}>
        {children}
      </div>
    );
  return (
    <motion.div
      {...rest}
      className={cls}
      variants={grow}
      initial={instant ? false : 'hidden'}
      animate="shown"
      exit="hidden"
      transition={transition}
      layout={layout ? 'position' : false}
      onUpdate={onFrame}
      // The grow animation clips vertically; the visible x keeps a bubble's side stamp, which hangs outside the item, in view.
      style={{ overflowY: 'clip', overflowX: 'visible' }}
    >
      {children}
    </motion.div>
  );
}

const narrow = () => typeof matchMedia === 'function' && matchMedia('(max-width: 639px)').matches;

/** A table row; its cells (<AnimatedCell>) do the height. On a narrow screen a row is a card, so its padding and margin shrink as well. */
export function AnimatedRow({
  children,
  rows,
  ...props
}: { rows: number } & Omit<React.ComponentProps<'tr'>, 'onDrag' | 'onDragStart' | 'onDragEnd' | 'onAnimationStart'>) {
  if (useReducedMotion()) return <tr {...props}>{children}</tr>;
  const card = narrow();
  const variants: Variants = card
    ? {
        hidden: { opacity: 0, paddingTop: 0, paddingBottom: 0, marginBottom: 0, borderTopWidth: 0, borderBottomWidth: 0 },
        shown: { opacity: 1, paddingTop: 8, paddingBottom: 8, marginBottom: 8, borderTopWidth: 1, borderBottomWidth: 1 },
      }
    : { hidden: { opacity: 0 }, shown: { opacity: 1 } };
  return (
    <motion.tr
      {...props}
      variants={variants}
      initial="hidden"
      animate="shown"
      exit="hidden"
      transition={transition}
      layout={rows <= LAYOUT_MAX ? 'position' : false}
    >
      {children}
    </motion.tr>
  );
}

/** A cell of an <AnimatedRow>: it goes between height 0 and the row's height, with a clip inside it that lets a table cell shrink below its text. */
export function AnimatedCell({
  children,
  ...props
}: Omit<React.ComponentProps<'td'>, 'onDrag' | 'onDragStart' | 'onDragEnd' | 'onAnimationStart'>) {
  if (useReducedMotion()) return <td {...props}>{children}</td>;
  const card = narrow();
  const cell: Variants = { hidden: { height: 0 }, shown: { height: card ? 'auto' : rowHeight() } };
  return (
    <motion.td {...props} variants={cell} transition={transition}>
      {card ? (
        children
      ) : (
        <motion.div variants={grow} transition={transition} style={{ overflow: 'hidden' }}>
          {children}
        </motion.div>
      )}
    </motion.td>
  );
}
