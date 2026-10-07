import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { colorOf, drawRows, styleOf } from '../src/screens/termspans.tsx';

describe('terminal colours', () => {
  it('maps indexes, the xterm cube, greys and #rrggbb, and refuses what is none of them', () => {
    expect(colorOf(2)).toBe('#6bbf72');
    expect(colorOf(16)).toBe('#000000');
    expect(colorOf(196)).toBe('#ff0000');
    expect(colorOf(231)).toBe('#ffffff');
    expect(colorOf(232)).toBe('#080808');
    expect(colorOf(255)).toBe('#eeeeee');
    expect(colorOf('#a1b2c3')).toBe('#a1b2c3');
    expect(colorOf('red')).toBeUndefined();
    expect(colorOf(256)).toBeUndefined();
  });

  it('turns flags into a text style, and inverse swaps the colours, defaults included', () => {
    expect(styleOf({ fg: 1, bold: true, italic: true, underline: true, strike: true, dim: true }, '#eee', '#0b0b0b')).toEqual({
      color: '#e0675a',
      fontWeight: '700',
      fontStyle: 'italic',
      opacity: 0.6,
      textDecorationLine: 'underline line-through',
    });
    expect(styleOf({ inverse: true }, '#eee', '#0b0b0b')).toEqual({ color: '#0b0b0b', backgroundColor: '#eee' });
    expect(styleOf({ inverse: true, fg: 2, bg: '#000000' }, '#eee', '#0b0b0b')).toEqual({ color: '#000000', backgroundColor: '#6bbf72' });
    expect(styleOf({}, '#eee', '#0b0b0b')).toEqual({});
  });

  const html = (rows: Parameters<typeof drawRows>[0]) => renderToStaticMarkup(<>{drawRows(rows, '#eee', '#0b0b0b')}</>);
  const runs = (h: string) => [...h.matchAll(/<(?:span|div)[^>]*color[^>]*>([^<]*)</g)].map((m) => m[1]);

  it('draws a run past the trimmed text at its column, after default blanks', () => {
    const h = html([{ text: 'abc', spans: [{ from: 8, to: 11, style: { bg: 1 } }] }]);
    expect(h.replace(/<[^>]*>/g, '')).toBe('abc' + ' '.repeat(5) + ' '.repeat(3));
    expect(h).toContain('abc     <'); // the gap is plain text before the run
    expect(runs(h)).toEqual(['   ']);
  });

  it('draws a run inside the text, and one that starts inside it and ends past it', () => {
    const inside = html([{ text: 'abcdef', spans: [{ from: 1, to: 3, style: { fg: 2 } }] }]);
    expect(inside.replace(/<[^>]*>/g, '')).toBe('abcdef');
    expect(runs(inside)).toEqual(['bc']);
    const past = html([{ text: 'abcd', spans: [{ from: 2, to: 6, style: { fg: 2 } }] }]);
    expect(past.replace(/<[^>]*>/g, '')).toBe('abcd  ');
    expect(runs(past)).toEqual(['cd  ']);
  });

  it('joins rows with a newline and draws a plain row as it is', () => {
    expect(html([{ text: 'one' }, { text: 'two', spans: [{ from: 0, to: 1, style: { bold: true } }] }]).replace(/<[^>]*>/g, '')).toBe(
      'one\ntwo',
    );
  });
});
