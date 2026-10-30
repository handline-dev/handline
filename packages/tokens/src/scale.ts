/** phone < `phone`, tablet `phone`..`pc`-1, PC >= `pc` (CSS px). */
export const breakpoints = { phone: 640, pc: 1024 } as const;
export type Breakpoint = 'phone' | 'tablet' | 'pc';
export const breakpointOf = (width: number): Breakpoint => (width < breakpoints.phone ? 'phone' : width < breakpoints.pc ? 'tablet' : 'pc');

/** Five type steps, one scale for the PC and the phone. Weights: 400, 500 for names and buttons, 600 for titles. */
export const typeSteps = ['xs', 'sm', 'md', 'lg', 'xl'] as const;
export type TypeStep = (typeof typeSteps)[number];
export const typeScale: Record<TypeStep, { size: number; line: number }> = {
  xs: { size: 11, line: 14 },
  sm: { size: 12.5, line: 18 },
  md: { size: 14, line: 22 },
  lg: { size: 16, line: 24 },
  xl: { size: 20, line: 28 },
};

/** One family: Pretendard for Korean, then the system face; ids and commands in mono. */
export const font = {
  sans: "-apple-system, 'Pretendard', 'Apple SD Gothic Neo', Inter, 'Segoe UI', system-ui, sans-serif",
  mono: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
  size: Object.fromEntries(typeSteps.map((s) => [s, typeScale[s].size])) as Record<TypeStep, number>,
} as const;

/** Space on a 4 px grid. */
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;

/** The gutter every list and detail uses: text starts 20 px in from the side and 16 px down from the top. */
export const gutter = { x: 20, y: 16 } as const;

/** Three control heights: a small button or chip, a button or tab, an input or a row. */
export const height = { s: 24, m: 28, l: 36 } as const;

/** Three corner radii: buttons, chips, rows and tabs (s); menus, popups and toasts (m); the chat box and dialogs (l). Round things (dots, switches) use 999. */
export const radius = { s: 6, m: 10, l: 16 } as const;

/** Motion: hover and press (fast), opening and closing (slow), in ms, and one easing. Reduced motion stops the turning and pulsing glyphs. */
export const motion = { fast: 120, slow: 200, ease: 'cubic-bezier(0.2, 0, 0, 1)' } as const;

/** Heights (CSS px) of the rows a list draws. A skeleton row and the real row both take them, so nothing shifts when data arrives. */
export const rowHeight = { line: 18, agent: 44, message: 56, task: 48, inbox: 56, account: 72, field: height.l } as const;

/** Sizes of the PC layout (CSS px). */
export const chrome = {
  'h-tabs': 40,
  'h-top': 44,
  'w-side': 240,
  'w-form': 640,
  'w-page': 960,
  'w-col': 768,
  'w-dialog': 460,
  icon: 16,
} as const;
