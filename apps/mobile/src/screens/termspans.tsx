// A styled terminal row drawn as nested Text runs (the web's terminal/spans.ts for the phone). Indexes 0-15 are the web theme's `--ansi-N`
// (apps/web/src/tokens.css), 16-255 the xterm cube and greys, `#rrggbb` as given; `inverse` swaps what is drawn, defaults included.
import type { ReactNode } from 'react';
import { Text, type TextStyle } from 'react-native';
import { rowPieces } from '@handline/app-core';
import type { Spans } from '@handline/client';

type Style = Spans[number]['style'];

const ANSI = [
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
const CUBE = [0, 95, 135, 175, 215, 255];
const hex = (n: number) => n.toString(16).padStart(2, '0');

/** A palette index or `#rrggbb` as a colour; undefined for a value that is neither. */
export function colorOf(c: number | string): string | undefined {
  if (typeof c === 'string') return /^#[0-9a-f]{6}$/i.test(c) ? c : undefined;
  if (!Number.isInteger(c) || c < 0 || c > 255) return undefined;
  if (c < 16) return ANSI[c];
  if (c < 232) {
    const i = c - 16;
    return `#${hex(CUBE[Math.floor(i / 36)]!)}${hex(CUBE[Math.floor(i / 6) % 6]!)}${hex(CUBE[i % 6]!)}`;
  }
  const g = 8 + (c - 232) * 10;
  return `#${hex(g)}${hex(g)}${hex(g)}`;
}

/** The text style of one run; `ink` and `ground` are the terminal's defaults, which `inverse` swaps. */
export function styleOf(st: Style, ink: string, ground: string): TextStyle {
  let fg = st.fg === undefined ? undefined : colorOf(st.fg);
  let bg = st.bg === undefined ? undefined : colorOf(st.bg);
  if (st.inverse) [fg, bg] = [bg ?? ground, fg ?? ink];
  const lines = [st.underline && 'underline', st.strike && 'line-through'].filter(Boolean).join(' ');
  return {
    ...(fg && { color: fg }),
    ...(bg && { backgroundColor: bg }),
    ...(st.bold && { fontWeight: '700' as const }),
    ...(st.italic && { fontStyle: 'italic' as const }),
    ...(st.dim && { opacity: 0.6 }),
    ...(lines && { textDecorationLine: lines as TextStyle['textDecorationLine'] }),
  };
}

/** The rows as Text children: plain strings, a nested Text per run that has a style (the layout is app-core's `rowPieces`). */
export function drawRows(rows: { text: string; spans?: Spans }[], ink: string, ground: string): ReactNode[] {
  const out: ReactNode[] = [];
  rows.forEach((r, y) => {
    if (y) out.push('\n');
    rowPieces(r).forEach((p, k) =>
      out.push(
        p.style ? (
          <Text key={`${y}:${k}`} style={styleOf(p.style, ink, ground)}>
            {p.text}
          </Text>
        ) : (
          p.text
        ),
      ),
    );
  });
  return out;
}
