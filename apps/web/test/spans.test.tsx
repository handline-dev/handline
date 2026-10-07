import { describe, expect, it } from 'vitest';
import { colorOf, cssOf, drawRows } from '../src/terminal/spans.ts';

describe('styled rows', () => {
  it('draws indexes 0-15 from the theme, the cube and greys as numbers, hex as given', () => {
    expect(colorOf(2)).toBe('var(--ansi-2)');
    expect(colorOf(15)).toBe('var(--ansi-15)');
    expect(colorOf(196)).toBe('#ff0000');
    expect(colorOf(244)).toBe('#808080');
    expect(colorOf('#10ab20')).toBe('#10ab20');
    expect(colorOf('red; x')).toBe('inherit');
  });

  it('swaps foreground and background for inverse, default colours included', () => {
    expect(cssOf({ fg: 1, bg: 4, inverse: true })).toBe('color:var(--ansi-4);background:var(--ansi-1)');
    expect(cssOf({ inverse: true })).toBe('color:var(--term-bg);background:var(--term-ink)');
    expect(cssOf({ bold: true, underline: true, strike: true })).toBe('font-weight:700;text-decoration:underline line-through');
  });

  it('counts runs in code points and pads a background run past the text', () => {
    const frag = drawRows(document, [
      { text: '😀 ok', spans: [{ from: 0, to: 1, style: { fg: 2 } }] },
      { text: 'bar', spans: [{ from: 0, to: 6, style: { bg: 4 } }] },
      { text: '😀', spans: [{ from: 0, to: 3, style: { bg: 4 } }] },
      { text: 'abc', spans: [{ from: 8, to: 11, style: { bg: 1 } }] },
    ]);
    const box = document.createElement('div');
    box.append(frag);
    const spans = [...box.querySelectorAll('span')];
    expect(spans.map((s) => s.textContent)).toEqual(['😀', 'bar   ', '😀  ', '   ']);
    expect(box.textContent).toBe('😀 ok\nbar   \n😀  \nabc     ' + '   ');
  });
});
