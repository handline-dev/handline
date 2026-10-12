// Design tokens. The values are the old PC web UI's, so nothing changes on screen when apps take them from here.

/** phone < `phone`, tablet `phone`..`pc`-1, PC >= `pc` (CSS px). */
export const breakpoints = { phone: 640, pc: 1024 } as const;
export type Breakpoint = 'phone' | 'tablet' | 'pc';
export const breakpointOf = (width: number): Breakpoint => (width < breakpoints.phone ? 'phone' : width < breakpoints.pc ? 'tablet' : 'pc');

export type Scheme = 'light' | 'dark';
export interface Palette {
  bg: string;
  panel: string;
  line: string;
  ink: string;
  muted: string;
  accent: string;
  accentInk: string;
  run: string;
  idle: string;
  wait: string;
  block: string;
  termBg: string;
  termInk: string;
}

const light: Palette = {
  bg: '#fafaf9',
  panel: '#fff',
  line: '#e4e2dd',
  ink: '#1c1b19',
  muted: '#77746d',
  accent: '#2f5fd0',
  accentInk: '#fff',
  run: '#2e9d57',
  idle: '#9b9890',
  wait: '#d98a14',
  block: '#d0412f',
  termBg: '#16150f',
  termInk: '#e8e6df',
};
export const colors: Record<Scheme, Palette> = {
  light,
  dark: {
    ...light,
    bg: '#151513',
    panel: '#1d1c19',
    line: '#2f2d28',
    ink: '#ecebe6',
    muted: '#98958c',
    accent: '#6f95ee',
    accentInk: '#0d1020',
    termBg: '#0d0d0b',
  },
};

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 } as const;
export const radius = { sm: 6, md: 8, lg: 10, pill: 999 } as const;
export const font = {
  sans: 'system-ui, sans-serif',
  mono: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
  size: { small: 12, body: 14, title: 16 },
} as const;

/** Heights (CSS px) of the rows a list draws. A skeleton row and the real row both take them, so nothing shifts when data arrives. */
export const rowHeight = { line: 18, agent: 44, message: 56, task: 48, inbox: 56, account: 72, field: 36 } as const;

const kebab = (s: string) => s.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);

/** The palette as CSS custom property declarations: `--bg: #fafaf9; --panel: #fff; ...` (accentInk becomes --accent-ink). */
export function cssVariables(scheme: Scheme): string {
  return Object.entries(colors[scheme])
    .map(([k, v]) => `--${kebab(k)}: ${v};`)
    .join(' ');
}
