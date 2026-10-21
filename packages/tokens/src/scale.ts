/** phone < `phone`, tablet `phone`..`pc`-1, PC >= `pc` (CSS px). */
export const breakpoints = { phone: 640, pc: 1024 } as const;
export type Breakpoint = 'phone' | 'tablet' | 'pc';
export const breakpointOf = (width: number): Breakpoint => (width < breakpoints.phone ? 'phone' : width < breakpoints.pc ? 'tablet' : 'pc');

/** Six type steps. The PC scale is Linear-dense (13 px body); the phone's body is 14 px for touch. */
export const typeSteps = ['hint', 'label', 'body', 'title', 'page', 'display'] as const;
export type TypeStep = (typeof typeSteps)[number];
export const typeScale: Record<'pc' | 'phone', Record<TypeStep, { size: number; line: number }>> = {
  pc: {
    hint: { size: 11, line: 14 },
    label: { size: 12, line: 16 },
    body: { size: 13, line: 18 },
    title: { size: 15, line: 20 },
    page: { size: 20, line: 26 },
    display: { size: 28, line: 34 },
  },
  phone: {
    hint: { size: 11, line: 14 },
    label: { size: 12, line: 16 },
    body: { size: 14, line: 20 },
    title: { size: 16, line: 22 },
    page: { size: 20, line: 26 },
    display: { size: 24, line: 30 },
  },
};

/** PC: Inter first; phone: the system face. */
export const font = {
  sansPc: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif",
  sans: 'system-ui, sans-serif',
  mono: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
  size: Object.fromEntries(typeSteps.map((s) => [s, typeScale.phone[s].size])) as Record<TypeStep, number>,
} as const;

/** Space on a 4 px grid. */
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;

/** chips, buttons and rows (ctl), cards and popups, dialogs, toggles and dots. */
export const radius = { chip: 4, ctl: 6, card: 8, dialog: 12, pill: 999 } as const;

/** Three elevations: a lifted chip, a popup, a dialog. The colour comes from the scheme (`shadowAlpha`). */
export const shadow = {
  1: { y: 2, blur: 8 },
  2: { y: 8, blur: 24 },
  3: { y: 16, blur: 48 },
} as const;

/** Motion: three durations (ms) and one easing. */
export const motion = { fast: 120, base: 200, slow: 320, ease: 'cubic-bezier(0.2, 0, 0, 1)' } as const;

/** Heights (CSS px) of the rows a list draws. A skeleton row and the real row both take them, so nothing shifts when data arrives. */
export const rowHeight = { line: 18, agent: 44, message: 56, task: 48, inbox: 56, account: 72, field: 36 } as const;

/** Sizes of the PC chrome (CSS px). */
export const chrome = {
  'h-row': 28,
  'h-row-2': 44,
  'h-tabs': 32,
  'h-bar': 24,
  'h-btn': 28,
  'h-btn-sm': 24,
  'h-btn-lg': 32,
  'h-input': 32,
  'h-field': 36,
  'trow-h': 34,
  'w-side': 240,
  'w-form': 640,
  'w-page': 960,
  'w-dialog': 460,
  icon: 16,
} as const;
