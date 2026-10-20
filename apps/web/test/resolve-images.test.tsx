// A Markdown memo file's relative pictures resolve to the memo's images by file name; a bad percent escape must never throw.
import { describe, expect, it } from 'vitest';
import { resolveImages } from '#src/bodyMarkdown.ts';

const files = [
  { id: 'a1', name: 'my shot.png' },
  { id: 'b2', name: '100%.png' },
  { id: 'c3', name: 'x.png' },
];

describe('resolveImages', () => {
  it('finds a file by its name, reading %20 as a space', () => {
    expect(resolveImages('![s](docs/img/my%20shot.png)', files)).toBe('![s](attachment:a1)');
    expect(resolveImages('![s](docs/img/x.png)', files)).toBe('![s](attachment:c3)');
  });

  it('does not throw on a malformed escape: the raw name still resolves, or the alt text stays', () => {
    expect(resolveImages('![odd](docs/img/x%zz.png)', files)).toBe('odd');
    expect(resolveImages('![odd](docs/img/x%zz.png)', [...files, { id: 'd4', name: 'x%zz.png' }])).toBe('![odd](attachment:d4)');
  });

  it('does not throw on a stray %, and finds a file whose name has one', () => {
    expect(resolveImages('![p](docs/img/100%.png)', files)).toBe('![p](attachment:b2)');
    expect(resolveImages('![p](docs/img/50%.png)', files)).toBe('p');
  });
});
