// The loading placeholder: shown only on first load, not before 150 ms (a fast answer shows no flash), and not for less than 300 ms
// once shown. No spinner anywhere: a region that waits draws this, a write that waits draws its pending mark.
import { createElement, useEffect, useRef, useState, type ReactNode } from 'react';
import { useData } from './provider.ts';
import { skeletons, type SkeletonName, type SkeletonNode } from './skeletons.ts';

export const SKELETON_DELAY_MS = 150;
export const SKELETON_MIN_MS = 300;

/** True while the skeleton should show for a `loading` that starts and stops. */
export function useSkeletonGate(loading: boolean, delayMs = SKELETON_DELAY_MS, minMs = SKELETON_MIN_MS): boolean {
  const [shown, setShown] = useState(false);
  const since = useRef(0);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    if (loading && !shown)
      timer = setTimeout(() => {
        since.current = Date.now();
        setShown(true);
      }, delayMs);
    else if (!loading && shown) timer = setTimeout(() => setShown(false), Math.max(0, minMs - (Date.now() - since.current)));
    return () => clearTimeout(timer);
  }, [loading, shown, delayMs, minMs]);
  return shown;
}

function draw(Box: NonNullable<ReturnType<typeof useData>['box']> | string, node: SkeletonNode, key: number): ReactNode {
  const make = (style: Record<string, unknown>, children?: ReactNode, extra: Record<string, unknown> = {}) =>
    createElement(Box as string, { key, style, ...extra }, children);
  if ('box' in node)
    return make({ width: node.box.w, height: node.box.h, borderRadius: node.box.r ?? 0 }, undefined, {
      className: 'sk',
      'data-sk': 'box',
    });
  if ('repeat' in node) return Array.from({ length: node.repeat }, (_, i) => draw(Box, node.of, i));
  const kids = ('row' in node ? node.row : node.col).map((n, i) => draw(Box, n, i));
  return make(
    { display: 'flex', flexDirection: 'row' in node ? 'row' : 'column', gap: node.gap ?? 0, ...(node.h ? { height: node.h } : {}) },
    kids,
  );
}

/** A view's loading shape, by name or as a tree. `loading` is the view's first-load flag; the gate applies the delay and the minimum. */
export function Skeleton(props: { shape: SkeletonName | SkeletonNode; loading: boolean; label: string }): ReactNode {
  const { box } = useData();
  const show = useSkeletonGate(props.loading);
  if (!show) return null;
  const node = typeof props.shape === 'string' ? skeletons[props.shape] : props.shape;
  return createElement(
    'div',
    { className: 'skeleton', role: 'status', 'aria-busy': true, 'aria-label': `Loading ${props.label}` },
    draw(box ?? 'div', node, 0),
  );
}

/** The shared look for the web: a quiet pulse that stops when the OS asks for reduced motion. The phone draws its own. */
export const SKELETON_CSS = `.sk { background: var(--line); animation: sk-pulse 1.4s ease-in-out infinite; }
@keyframes sk-pulse { 50% { opacity: 0.5; } }
@media (prefers-reduced-motion: reduce) { .sk { animation: none; } }`;
