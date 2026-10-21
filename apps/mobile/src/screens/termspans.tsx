// A styled terminal row drawn as nested Text runs (the web's terminal/spans.ts for the phone). Indexes 0-15 are the tokens' `ansi`
// (the web's `--ansi-N`), 16-255 the xterm cube and greys, `#rrggbb` as given; `inverse` swaps what is drawn, defaults included.
import type { ReactNode } from 'react';
import { Text, type TextStyle } from 'react-native';
import { rowPieces, xtermColor } from '@handline/app-core';
import type { Spans } from '@handline/client';
import { ansi } from '@handline/tokens';

type Style = Spans[number]['style'];

/** A palette index or `#rrggbb` as a colour; undefined for a value that is neither. */
export function colorOf(c: number | string): string | undefined {
  if (typeof c === 'string') return /^#[0-9a-f]{6}$/i.test(c) ? c : undefined;
  if (!Number.isInteger(c) || c < 0 || c > 255) return undefined;
  if (c < 16) return ansi[c];
  return xtermColor(c);
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
