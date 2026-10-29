import { ansi, scheme, type Colors } from '#src/palette.ts';
import { chrome, font, gutter, height, motion, radius, space, typeScale, typeSteps } from '#src/scale.ts';

const colorVars = (c: Colors, indent: string): string[] => {
  const v: [string, string][] = [
    ['bg-0', c.surface[0]],
    ['bg-1', c.surface[1]],
    ['bg-2', c.surface[2]],
    ['bg-3', c.surface[3]],
    ['line', c.line],
    ['line-strong', c.lineStrong],
    ['ink', c.ink[0]],
    ['ink-2', c.ink[1]],
    ['ink-3', c.ink[2]],
    ['ink-4', c.ink[3]],
    ['acc', c.accent],
    ['acc-ink', c.accentInk],
    ['acc-wash', c.accentWash],
    ['on-acc', c.onAccent],
    ['s-working', c.working],
    ['s-wait', c.wait],
    ['s-bad', c.bad],
    ['s-ok', c.ok],
    ['cmd', c.cmd],
    ['term-bg', c.termBg],
    ['term-ink', c.termInk],
    ['code-bg', c.codeBg],
    ['code-ink', c.codeInk],
    ['shadow', c.shadow],
    ['scrim', c.scrim],
    ['hover-bg', c.hover],
    ['press-bg', c.press],
  ];
  return v.map(([k, x]) => `${indent}--${k}: ${x};`);
};

const spaceNames = Object.keys(space) as (keyof typeof space)[];

/** The text of apps/web/src/tokens.css, written by `pnpm --filter @handline/tokens build:css`. */
export function tokensCss(): string {
  const px = (n: number) => `${n}px`;
  const out: string[] = [
    '/* GENERATED from packages/tokens (`pnpm --filter @handline/tokens build:css`); edit the values there. The only place a colour, a size, a space or a radius is given a value.',
    '   Dark is the default; light follows the OS and `data-theme` on <html> overrides both. */',
    ':root {',
    '  /* surfaces: canvas, sidebar and bars, cards and inputs, hover and selected */',
    ...colorVars(scheme.dark, '  '),
    '  color-scheme: dark;',
    '}',
    '@media (prefers-color-scheme: light) {',
    "  :root:not([data-theme='dark']) {",
    ...colorVars(scheme.light, '    '),
    '    color-scheme: light;',
    '  }',
    '}',
    ":root[data-theme='light'] {",
    ...colorVars(scheme.light, '  '),
    '  color-scheme: light;',
    '}',
    '',
    ':root {',
    '  /* type: one family, five sizes */',
    `  --sans: ${font.sans};`,
    `  --mono: ${font.mono.replace(/"/g, "'")};`,
    ...typeSteps.map((s) => `  --fs-${s}: ${px(typeScale[s].size)};`),
    ...typeSteps.map((s) => `  --t-${s}: ${s === 'lg' || s === 'xl' ? 600 : 400} var(--fs-${s})/${px(typeScale[s].line)} var(--sans);`),
    '  --t-name: 500 var(--fs-md)/18px var(--sans);',
    '  --t-mono: 400 12px/16px var(--mono);',
    '  /* space on a 4 px grid, and the gutter lists and details start their text at */',
    ...spaceNames.map((k, i) => `  --sp-${i + 1}: ${px(space[k])};`),
    `  --gx: ${px(gutter.x)};`,
    `  --gy: ${px(gutter.y)};`,
    '  /* three control heights and three radii; round things use 999px */',
    ...Object.entries(height).map(([k, v]) => `  --h-${k}: ${px(v)};`),
    ...Object.entries(radius).map(([k, v]) => `  --r-${k}: ${px(v)};`),
    '  /* motion */',
    `  --dur-fast: ${motion.fast}ms;`,
    `  --dur-slow: ${motion.slow}ms;`,
    `  --ease: ${motion.ease};`,
    '  /* sizes of the layout */',
    ...Object.entries(chrome).map(([k, v]) => `  --${k}: ${px(v)};`),
    '}',
    '',
    ':root {',
    "  /* The 16 colours a styled row's indexes 0-15 draw in, on the terminal's dark ground in both themes. */",
    ...ansi.map((c, i) => `  --ansi-${i}: ${c};`),
    '}',
    '',
  ];
  return out.join('\n');
}
