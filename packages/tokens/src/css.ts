import { ansi, scheme, type Colors } from '#src/palette.ts';
import { chrome, font, motion, radius, shadow, space, typeScale, typeSteps } from '#src/scale.ts';

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
    ['primary', c.primary],
    ['on-primary', c.onPrimary],
    ['focus', c.accent],
    ['link', c.accentStrong],
    ['s-working', c.working],
    ['s-wait', c.wait],
    ['s-bad', c.bad],
    ['s-ok', c.ok],
    ['term-bg', c.termBg],
    ['term-ink', c.termInk],
    ['shadow-1', `0 ${shadow[1].y}px ${shadow[1].blur}px ${c.shadowAlpha}`],
    ['shadow-2', `0 ${shadow[2].y}px ${shadow[2].blur}px ${c.shadowAlpha}`],
    ['shadow-3', `0 ${shadow[3].y}px ${shadow[3].blur}px ${c.shadowAlpha}`],
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
    '  /* type: one sans and one mono; six sizes */',
    `  --sans: ${font.sansPc};`,
    `  --mono: ${font.mono.replace(/"/g, "'")};`,
    ...typeSteps.map((s) => `  --fs-${s}: ${px(typeScale.pc[s].size)};`),
    ...typeSteps.map((s) => {
      const weight = s === 'title' || s === 'page' || s === 'display' ? 600 : 400;
      return `  --t-${s}: ${weight} var(--fs-${s})/${px(typeScale.pc[s].line)} var(--sans);`;
    }),
    '  --t-name: 500 var(--fs-body)/18px var(--sans);',
    '  --t-mono: 400 12px/16px var(--mono);',
    '  /* space on a 4 px grid */',
    ...spaceNames.map((k, i) => `  --sp-${i + 1}: ${px(space[k])};`),
    '  /* radius: chips, buttons and rows, cards and popups, dialogs, toggles and dots */',
    `  --r-chip: ${px(radius.chip)};`,
    `  --r-ctl: ${px(radius.ctl)};`,
    `  --r-card: ${px(radius.card)};`,
    `  --r-dialog: ${px(radius.dialog)};`,
    `  --r-pill: ${px(radius.pill)};`,
    '  /* motion */',
    `  --dur-fast: ${motion.fast}ms;`,
    `  --dur-base: ${motion.base}ms;`,
    `  --dur-slow: ${motion.slow}ms;`,
    `  --ease: ${motion.ease};`,
    '  /* sizes of chrome */',
    ...Object.entries(chrome).map(([k, v]) => `  --${k}: ${px(v)};`),
    '}',
    '',
    '/* Legacy names of the screens not yet moved to the tokens; each group that touches a screen drops the aliases it stops using. */',
    ':root {',
    '  --bg: var(--bg-0);',
    '  --panel: var(--bg-2);',
    '  --muted: var(--ink-2);',
    '  --accent: var(--primary);',
    '  --accent-ink: var(--on-primary);',
    '  --run: var(--s-working);',
    '  --wait: var(--s-wait);',
    '  --block: var(--s-bad);',
    '  --idle: var(--ink-2);',
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
