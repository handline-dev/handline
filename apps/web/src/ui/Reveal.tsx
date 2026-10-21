// What opens and closes: its children grow from height 0 to auto while they fade in, and shrink back on the way out. Only height and opacity
// move, never scale or translate. Reduced motion shows and hides at once. A part that is a `motion` element itself uses `useOpenMotion` under an <AnimatedList>.
import type { ComponentProps, ReactNode } from 'react';
import { motion, useReducedMotion, type Transition, type Variants } from 'framer-motion';
import { AnimatedList } from '#src/AnimatedList.tsx';

export const OPEN_TIME = 0.18;
export const OPEN_TRANSITION: Transition = { duration: OPEN_TIME, ease: 'easeOut' };
/** The variants of a part that opens: give them to a `motion` element with `initial="hidden" animate="shown" exit="hidden"`.
 *  `--o` goes 0 to 1 beside the height: a box with margins and padding scales them with `calc(8px * var(--o, 1))`, so no gap is left behind. */
export const GROW: Variants = {
  hidden: { height: 0, opacity: 0, '--o': 0 },
  shown: { height: 'auto', opacity: 1, '--o': 1 },
};
/** The variants of a part that only fades (a backdrop, a floating menu whose own height the library measures). */
export const FADE: Variants = { hidden: { opacity: 0 }, shown: { opacity: 1 } };

/** The props that put a `motion` element on the open/close animation; under reduced motion none, so it renders and leaves at once. */
export function useOpenMotion(variants: Variants = GROW) {
  return useReducedMotion()
    ? {}
    : ({ variants, initial: 'hidden', animate: 'shown', exit: 'hidden', transition: OPEN_TRANSITION } as const);
}

/** `show` decides whether `children` are there. The clip (`overflow: hidden`, which also holds the children's margins inside) keeps the content inside the shrinking box. */
export function Reveal({ show, children, ...rest }: { show: boolean; children: ReactNode } & Omit<ComponentProps<'div'>, 'children' | 'onDrag' | 'onDragStart' | 'onDragEnd' | 'onAnimationStart'>) {
  const open = useOpenMotion();
  return (
    <AnimatedList>
      {show ? (
        <motion.div {...rest} {...open} style={{ overflow: 'hidden' }}>
          {children}
        </motion.div>
      ) : null}
    </AnimatedList>
  );
}
