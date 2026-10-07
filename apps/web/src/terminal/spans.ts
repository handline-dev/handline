// Colour for the text grid: a styled row's runs drawn as spans. Indexes 0-15 are the theme's (`--ansi-N`), 16-255 the xterm cube and
// greys, `#rrggbb` as given; `inverse` swaps what is drawn, so the default colours swap too.
import { rowPieces } from '@handline/app-core';
import type { Spans } from '@handline/client';

type Style = Spans[number]['style'];

const CUBE = [0, 95, 135, 175, 215, 255];
const hex = (n: number) => n.toString(16).padStart(2, '0');

/** A palette index or `#rrggbb` as a CSS colour. */
export function colorOf(c: number | string): string {
  if (typeof c === 'string') return /^#[0-9a-f]{6}$/i.test(c) ? c : 'inherit';
  if (c < 16) return `var(--ansi-${c})`;
  if (c < 232) {
    const i = c - 16;
    return `#${hex(CUBE[Math.floor(i / 36)]!)}${hex(CUBE[Math.floor(i / 6) % 6]!)}${hex(CUBE[i % 6]!)}`;
  }
  const g = 8 + (c - 232) * 10;
  return `#${hex(g)}${hex(g)}${hex(g)}`;
}

/** The CSS declarations of one run, or '' for the default style. */
export function cssOf(st: Style): string {
  let fg = st.fg === undefined ? undefined : colorOf(st.fg),
    bg = st.bg === undefined ? undefined : colorOf(st.bg);
  if (st.inverse) [fg, bg] = [bg ?? 'var(--term-bg)', fg ?? 'var(--term-ink)'];
  const css: string[] = [];
  if (fg) css.push(`color:${fg}`);
  if (bg) css.push(`background:${bg}`);
  if (st.bold) css.push('font-weight:700');
  if (st.dim) css.push('opacity:.6');
  if (st.italic) css.push('font-style:italic');
  const lines = [st.underline && 'underline', st.strike && 'line-through'].filter(Boolean);
  if (lines.length) css.push(`text-decoration:${lines.join(' ')}`);
  return css.join(';');
}

/** The rows as DOM: plain text nodes, with a span per run that has a style. Runs may reach past the text, into blank cells with a background. */
export function drawRows(doc: Document, rows: { text: string; spans?: Spans }[]): DocumentFragment {
  const out = doc.createDocumentFragment();
  rows.forEach((r, y) => {
    if (y) out.append('\n');
    for (const p of rowPieces(r)) {
      if (!p.style) out.append(p.text);
      else {
        const el = doc.createElement('span');
        el.style.cssText = cssOf(p.style);
        el.textContent = p.text;
        out.append(el);
      }
    }
  });
  return out;
}
