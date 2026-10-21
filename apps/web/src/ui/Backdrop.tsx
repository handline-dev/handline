// The dimmed, blurred page behind a dialog or the palette, with its content hanging 12vh from the top. It fades in and out under an <AnimatedList>.
import type { ComponentProps } from 'react';
import { motion } from 'framer-motion';
import { FADE, useOpenMotion } from '#src/ui/Reveal.tsx';

export function Backdrop({ className, ...rest }: Omit<ComponentProps<'div'>, 'onDrag' | 'onDragStart' | 'onDragEnd' | 'onAnimationStart'>) {
  return (
    <motion.div
      {...useOpenMotion(FADE)}
      {...rest}
      className={`ui-backdrop fixed inset-0 z-40 flex items-start justify-center bg-[var(--scrim)] pt-[12vh] backdrop-blur-[3px]${className ? ` ${className}` : ''}`}
    />
  );
}
