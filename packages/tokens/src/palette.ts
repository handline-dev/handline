export type Scheme = 'light' | 'dark';

/** One scheme's colours. Surfaces step up from the canvas (0) to the hover and selected fill (3); ink steps down from the name (1) to the hint (3). */
export interface Colors {
  surface: readonly [string, string, string, string];
  line: string;
  lineStrong: string;
  ink: readonly [string, string, string];
  /** The solid control fill (primary button, switch on) and the ink on it. */
  primary: string;
  onPrimary: string;
  /** The one calm blue: focus ring, links and the mobile accent. `accentStrong` is the link on text. */
  accent: string;
  accentStrong: string;
  /** Okabe-Ito state hues; each sits beside a glyph and is never the only signal. */
  working: string;
  wait: string;
  bad: string;
  ok: string;
  termBg: string;
  termInk: string;
  /** Code and diff blocks in text (a proposal's change, a card's code): the terminal's colours in dark, a soft gray in light. */
  codeBg: string;
  codeInk: string;
  shadowAlpha: string;
  scrim: string;
  /** The one tint over a hovered item, and the step darker over a pressed one: light on dark, dark on light, over any ground. */
  hover: string;
  press: string;
}

const dark: Colors = {
  surface: ['#0b0b0d', '#121215', '#1a1a1e', '#26262c'],
  line: '#ffffff12',
  lineStrong: '#ffffff26',
  ink: ['#fafafa', '#a1a1a1', '#6b6b6b'],
  primary: '#4a6fc4',
  onPrimary: '#ffffff',
  accent: '#86a0dd',
  accentStrong: '#9fb5e6',
  working: '#56b4e9',
  wait: '#f0b429',
  bad: '#e8743b',
  ok: '#3fbf9b',
  termBg: '#0a0a0b',
  termInk: '#e8e6df',
  codeBg: '#0a0a0b',
  codeInk: '#e8e6df',
  shadowAlpha: '#0006',
  scrim: '#0008',
  hover: '#ffffff0f',
  press: '#ffffff1a',
};

const light: Colors = {
  surface: ['#ffffff', '#f7f7f8', '#efeff1', '#e4e4e8'],
  line: '#e4e4e7',
  lineStrong: '#c9c9d0',
  ink: ['#2b2b30', '#52525b', '#8a8a94'],
  primary: '#3d62b5',
  onPrimary: '#ffffff',
  accent: '#3d62b5',
  accentStrong: '#2f4f98',
  working: '#0072b2',
  wait: '#b87900',
  bad: '#c2410c',
  ok: '#00875f',
  termBg: '#16150f',
  termInk: '#e8e6df',
  codeBg: '#f3f2ee',
  codeInk: '#2b2b30',
  shadowAlpha: '#0002',
  scrim: '#00000059',
  hover: '#0000000d',
  press: '#00000017',
};

export const scheme: Record<Scheme, Colors> = { light, dark };

/** The 16 colours a styled terminal row's indexes 0-15 draw in, on the terminal's dark ground in both schemes. */
export const ansi = [
  '#3a3935',
  '#e0675a',
  '#6bbf72',
  '#d9ae4c',
  '#6f95ee',
  '#c383d6',
  '#5cbfc9',
  '#cfcdc5',
  '#6d6b64',
  '#f08a7e',
  '#8fd896',
  '#ecc86e',
  '#93b3f6',
  '#dba3ea',
  '#86d6df',
  '#f4f2ec',
] as const;

/** Ink on a state-coloured fill (a count badge, a yes button): white in both schemes. */
export const onFill = '#ffffff';

/** The mobile screens' flat view of a scheme (`usePalette`). */
export interface Palette {
  bg: string;
  panel: string;
  raised: string;
  line: string;
  lineStrong: string;
  ink: string;
  muted: string;
  hint: string;
  accent: string;
  accentInk: string;
  run: string;
  idle: string;
  wait: string;
  block: string;
  termBg: string;
  termInk: string;
  codeBg: string;
  codeInk: string;
  scrim: string;
  /** The press feedback of a tapped row (a tint over the row). */
  press: string;
  onFill: string;
}

const flat = (c: Colors): Palette => ({
  bg: c.surface[0],
  panel: c.surface[1],
  raised: c.surface[2],
  line: c.line,
  lineStrong: c.lineStrong,
  ink: c.ink[0],
  muted: c.ink[1],
  hint: c.ink[2],
  accent: c.accent,
  accentInk: c.surface[0],
  run: c.ok,
  idle: c.ink[2],
  wait: c.wait,
  block: c.bad,
  termBg: c.termBg,
  termInk: c.termInk,
  codeBg: c.codeBg,
  codeInk: c.codeInk,
  scrim: c.scrim,
  press: c.press,
  onFill,
});

export const colors: Record<Scheme, Palette> = { light: flat(light), dark: flat(dark) };
