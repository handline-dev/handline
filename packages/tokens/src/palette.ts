export type Scheme = 'light' | 'dark';

/** One scheme's colours. Surfaces step up from the canvas (0) to the hover and selected fill (3); ink steps down from the name (1) to the hint (3). */
export interface Colors {
  surface: readonly [string, string, string, string];
  line: string;
  lineStrong: string;
  ink: readonly [string, string, string, string];
  /** The one blue, for what can be clicked: the primary button and the sender's bubble (`accent`), links and ids (`accentInk`), the ink on the fill (`onAccent`), a faint ground for a chosen item (`accentWash`). It is also the focus ring. */
  accent: string;
  accentInk: string;
  accentWash: string;
  onAccent: string;
  /** Okabe-Ito state hues; each sits beside a glyph (apps/web/src/ui/State.tsx) and is never the only signal. */
  working: string;
  wait: string;
  bad: string;
  ok: string;
  /** Command mode in the chat box: a message that starts with ! is sent as typed. */
  cmd: string;
  termBg: string;
  termInk: string;
  /** Code and diff blocks in text (a proposal's change, a card's code): the terminal's colours in dark, a soft gray in light. */
  codeBg: string;
  codeInk: string;
  /** The shadow of a floating layer (menu, popup, toast, dialog): the only shadow there is. */
  shadow: string;
  scrim: string;
  /** The one tint over a hovered item, and the step darker over a pressed one: light on dark, dark on light, over any ground. */
  hover: string;
  press: string;
}

const dark: Colors = {
  surface: ['#121214', '#17171a', '#1f1f23', '#2a2a30'],
  line: '#26262b',
  lineStrong: '#3a3a42',
  ink: ['#ececef', '#a6a6b0', '#73737d', '#4d4d55'],
  accent: '#5b85f0',
  accentInk: '#86a6f5',
  accentWash: '#1b2440',
  onAccent: '#ffffff',
  working: '#56b4e9',
  wait: '#e69f00',
  bad: '#e8735b',
  ok: '#3fb37f',
  cmd: '#f06fb0',
  termBg: '#0a0a0b',
  termInk: '#e8e6df',
  codeBg: '#0a0a0b',
  codeInk: '#e8e6df',
  shadow: '0 10px 30px #00000080',
  scrim: '#0008',
  hover: '#ffffff0f',
  press: '#ffffff1a',
};

const light: Colors = {
  surface: ['#ffffff', '#f7f7f8', '#f0f0f2', '#e6e6ea'],
  line: '#e7e7eb',
  lineStrong: '#d2d2d9',
  ink: ['#1d1d21', '#55555e', '#8b8b95', '#b4b4bc'],
  accent: '#2f5bd3',
  accentInk: '#2a51bd',
  accentWash: '#eaf0fd',
  onAccent: '#ffffff',
  working: '#0072b2',
  wait: '#c77c00',
  bad: '#c1442e',
  ok: '#1f8a5b',
  cmd: '#c2377f',
  termBg: '#16150f',
  termInk: '#e8e6df',
  codeBg: '#f3f2ee',
  codeInk: '#1d1d21',
  shadow: '0 8px 28px #14141e1f, 0 1px 2px #14141e0f',
  scrim: '#14141852',
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
  link: string;
  run: string;
  idle: string;
  wait: string;
  block: string;
  /** Fine, done: the green. */
  ok: string;
  /** Command mode in the chat box (a message that starts with !). */
  cmd: string;
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
  link: c.accentInk,
  accentInk: c.onAccent,
  run: c.working,
  idle: c.ink[2],
  wait: c.wait,
  block: c.bad,
  ok: c.ok,
  cmd: c.cmd,
  termBg: c.termBg,
  termInk: c.termInk,
  codeBg: c.codeBg,
  codeInk: c.codeInk,
  scrim: c.scrim,
  press: c.press,
  onFill,
});

export const colors: Record<Scheme, Palette> = { light: flat(light), dark: flat(dark) };
